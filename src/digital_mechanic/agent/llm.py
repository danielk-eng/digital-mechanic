"""OpenAI-compatible LLM client: tool calling, vision, token accounting, optional Opik tracing.

The API key is read from settings (env or key file) and is never logged or returned.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ..config import Settings


class LlmUnavailable(Exception):
    pass


class _Attempt:
    def __init__(self) -> None:
        self.first = threading.Event()
        self.done = threading.Event()
        self.cancel = False
        self.result: Any = None
        self.error: BaseException | None = None


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    per_step: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        avg = self.seconds / self.calls if self.calls else 0
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "seconds": round(self.seconds, 1), "avg_seconds": round(avg, 1), "last": self.per_step[-20:]}


class Llm:
    def __init__(self, s: Settings):
        self.s = s
        self.usage = Usage()
        self._lock = threading.Lock()
        self._client = None
        self._pool = None
        self._opik = None
        if s.comet_api_key:
            try:  # optional dependency
                import os

                os.environ.setdefault("OPIK_API_KEY", s.comet_api_key)
                os.environ.setdefault("OPIK_PROJECT_NAME", s.opik_project)
                from opik.integrations.openai import track_openai  # type: ignore

                self._opik = track_openai
            except Exception:
                self._opik = None

    @property
    def available(self) -> bool:
        return bool(self.s.api_key())

    def _cli(self):
        if self._client is None:
            if not self.available:
                raise LlmUnavailable("no api key")
            from openai import OpenAI

            c = OpenAI(base_url=self.s.llm_base_url, api_key=self.s.api_key(), timeout=self.s.llm_timeout_s, max_retries=1)
            self._client = self._opik(c) if self._opik else c
        return self._client

    def _create(self, kw: dict) -> Any:
        """Hedged request: if the free model has not answered in `llm_hedge_s`, send the same request again
        and take whichever finishes first (cuts the long tail of provider latency; the loser is ignored)."""
        hedge = self.s.llm_hedge_s
        if hedge <= 0:
            return self._cli().chat.completions.create(**kw)
        import concurrent.futures as cf

        if self._pool is None:
            self._pool = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="llm")
        futs = [self._pool.submit(self._cli().chat.completions.create, **kw)]
        done, _ = cf.wait(futs, timeout=hedge)
        if not done:
            futs.append(self._pool.submit(self._cli().chat.completions.create, **kw))
        err: BaseException | None = None
        while futs:
            done, _ = cf.wait(futs, return_when=cf.FIRST_COMPLETED)
            for f in done:
                futs.remove(f)
                if f.exception() is None:
                    return f.result()
                err = f.exception()
        raise err  # type: ignore[misc]

    # ------------------------------------------------------------ streaming race
    def _run_stream(self, kw: dict, att: "_Attempt", on_text, is_winner) -> None:
        """Consume one streamed completion into a message object (content + tool calls)."""
        try:
            stream = self._cli().chat.completions.create(**kw, stream=True, stream_options={"include_usage": True})
            content: list[str] = []
            calls: dict[int, dict] = {}
            usage = None
            for ch in stream:
                if att.cancel:
                    try:
                        stream.close()
                    except Exception:
                        pass
                    return
                if getattr(ch, "usage", None):
                    usage = ch.usage
                if not ch.choices:
                    continue
                d = ch.choices[0].delta
                if d is None:
                    continue
                got = False
                if d.content:
                    content.append(d.content)
                    got = True
                for tc in getattr(d, "tool_calls", None) or []:
                    e = calls.setdefault(tc.index or 0, {"id": None, "name": "", "args": ""})
                    if tc.id:
                        e["id"] = tc.id
                    if tc.function is not None:
                        e["name"] += tc.function.name or ""
                        e["args"] += tc.function.arguments or ""
                    got = True
                if got or getattr(d, "reasoning_content", None):
                    att.first.set()
                if d.content and on_text and is_winner(att):
                    try:
                        on_text("".join(content))
                    except Exception:
                        pass
            tcs = [SimpleNamespace(id=e["id"] or f"call_{i}", type="function", function=SimpleNamespace(name=e["name"], arguments=e["args"] or "{}"))
                   for i, e in sorted(calls.items())]
            att.result = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="".join(content) or None, tool_calls=tcs or None))], usage=usage)
        except Exception as e:  # never leak the key: only the type is reported upstream
            att.error = e
        finally:
            att.first.set()
            att.done.set()

    def _race(self, kw: dict, on_text=None) -> Any:
        """Streamed request with a time-to-first-token hedge: if no token arrived in `llm_hedge_ttft_s`
        (the free model sometimes sits in a queue), a second identical request is sent; the first one that
        starts streaming wins and the other is cancelled. Output length no longer triggers useless duplicates."""
        import concurrent.futures as cf

        if self._pool is None:
            self._pool = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="llm")
        hedge = max(0.5, self.s.llm_hedge_ttft_s)
        atts: list[_Attempt] = []
        win: list[_Attempt | None] = [None]

        def start() -> None:
            a = _Attempt()
            atts.append(a)
            self._pool.submit(self._run_stream, kw, a, on_text, lambda x: win[0] is x)

        t0 = time.time()
        start()
        while True:
            ok = [a for a in atts if a.first.is_set() and a.error is None]
            if ok:
                w = win[0] = ok[0]
                for a in atts:
                    if a is not w:
                        a.cancel = True
                if not w.done.wait(self.s.llm_timeout_s):
                    w.cancel = True
                    raise TimeoutError("llm stream timeout")
                if w.error is None and w.result is not None:
                    if on_text and w.result.choices[0].message.content:
                        try:  # chunks that arrived before the winner was picked
                            on_text(w.result.choices[0].message.content)
                        except Exception:
                            pass
                    return w.result
                win[0] = None
                atts.remove(w)
                if not atts:
                    raise w.error or RuntimeError("empty stream")
                continue
            el = time.time() - t0
            failed = [a for a in atts if a.error is not None]
            if failed and len(failed) == len(atts):
                if len(atts) >= 3:
                    raise failed[-1].error  # type: ignore[misc]
                start()
            elif len(atts) < 3 and el > hedge * (1 if len(atts) == 1 else 2.5):
                start()
            if el > self.s.llm_timeout_s:
                for a in atts:
                    a.cancel = True
                raise TimeoutError("llm first token timeout")
            time.sleep(0.05)

    def _account(self, step: str, r: Any, dt: float) -> int:
        u = getattr(r, "usage", None)
        pt, ct = (u.prompt_tokens or 0, u.completion_tokens or 0) if u else (0, 0)
        with self._lock:
            self.usage.calls += 1
            self.usage.prompt_tokens += pt
            self.usage.completion_tokens += ct
            self.usage.seconds += dt
            self.usage.per_step.append({"step": step, "prompt_tokens": pt, "completion_tokens": ct, "seconds": round(dt, 2)})
        return pt + ct

    def chat(self, step: str, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 1800,
             temperature: float = 0.1, model: str | None = None, on_text=None) -> tuple[Any, int]:
        """Return (assistant message, tokens used)."""
        t = time.time()
        kw: dict[str, Any] = dict(model=model or self.s.llm_model, messages=messages, temperature=temperature, max_tokens=max_tokens)
        if tools:
            kw["tools"] = tools
        if self.s.llm_thinking.lower() in ("off", "0", "false", "disabled"):
            kw["extra_body"] = {"thinking": {"type": "disabled"}}
        last: Exception | None = None
        for attempt in range(2):
            try:
                r = self._race(kw, on_text) if self.s.llm_stream else self._create(kw)
                break
            except LlmUnavailable:
                raise
            except Exception as e:  # network, rate limit, 5xx: never leak the key
                last = e
                time.sleep(1.5 * (attempt + 1))
        else:
            raise LlmUnavailable(type(last).__name__) from None
        tok = self._account(step, r, time.time() - t)
        return r.choices[0].message, tok

    def json(self, step: str, system: str, user: str, max_tokens: int = 2000) -> dict:
        msg, _ = self.chat(step, [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=max_tokens, temperature=0)
        return parse_json(msg.content or "")

    def vision(self, step: str, prompt: str, image_b64: str, mime: str = "image/jpeg", max_tokens: int = 900) -> str:
        msg, _ = self.chat(step, [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}}]}],
            max_tokens=max_tokens, temperature=0, model=self.s.llm_vision_model or None)
        return (msg.content or "").strip()


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text.strip(), re.S)
    if not m:
        raise LlmUnavailable("no json in answer")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        raise LlmUnavailable("bad json") from None
