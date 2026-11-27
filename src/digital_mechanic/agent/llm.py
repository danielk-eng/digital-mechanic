"""OpenAI-compatible LLM client: tool calling, vision, token accounting, optional Opik tracing.

The API key is read from settings (env or key file) and is never logged or returned.
"""

from __future__ import annotations

import contextvars
import json
import re
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from .. import pii
from ..config import Settings


class LlmUnavailable(Exception):
    pass


class _Attempt:
    def __init__(self) -> None:
        self.first = threading.Event()
        self.done = threading.Event()
        self.cancel = False
        self.last = time.time()  # last chunk (content, tool call or reasoning): a silent winner is a stalled stream
        self.result: Any = None
        self.error: BaseException | None = None


def cached_tokens(u: Any) -> int:
    """Prompt tokens served from the provider's prefix cache (billed at the cached-input price), when the provider reports it:
    OpenAI-style usage.prompt_tokens_details.cached_tokens or DeepSeek's usage.prompt_cache_hit_tokens; else 0."""
    if u is None:
        return 0
    det = getattr(u, "prompt_tokens_details", None)
    v = (det.get("cached_tokens") if isinstance(det, dict) else getattr(det, "cached_tokens", None)) if det is not None else None
    if v is None:
        v = getattr(u, "prompt_cache_hit_tokens", None)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


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



# Country of the person behind the current request (set by the web layer): picks the primary provider.
REQUEST_COUNTRY: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_country", default=None)
REQUEST_PRIMARY: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_primary", default=None)  # per-company switch
# Absolute time (time.time()) by which the current user turn must have its LLM answers (Agent.handle sets it): no call
# starts or waits past it, the agent then answers from the manual pages without the model -- never a hung chat.
REQUEST_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("request_deadline", default=None)
# Depersonalization before the text leaves the server (per company; None = the stand setting PII_MASKING, on by default)
REQUEST_PII: contextvars.ContextVar[bool | None] = contextvars.ContextVar("request_pii", default=None)
# Fallback journal of the current turn (Agent.handle sets a list): which model answered, switches and why
REQUEST_LLM_EVENTS: contextvars.ContextVar[list | None] = contextvars.ContextVar("request_llm_events", default=None)
REQUEST_CONV: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_conv", default=None)
# set by the agent when the primary loops on tool calls: the rest of the turn goes to the reserve first
REQUEST_FORCE_FALLBACK: contextvars.ContextVar[bool] = contextvars.ContextVar("request_force_fallback", default=False)

PROV_RU = {"tokenrouter": "DeepSeek (TokenRouter, free)", "cloudru": "GigaChat-4 (Cloud.ru, free)", "quality": "сервер «Подробнее»"}


def llm_event(**kw: Any) -> None:
    ev = REQUEST_LLM_EVENTS.get()
    if ev is not None:
        ev.append(kw)


def degenerate_text(text: str) -> str | None:
    """Loop inside one answer: the same line 3+ times, or one 6-word sequence repeated so that repeats cover >= 30 % of the text."""
    t = (text or "").strip()
    lines = [ln.strip() for ln in t.splitlines() if len(ln.strip()) >= 20]
    if lines:
        from collections import Counter

        ln, n = Counter(lines).most_common(1)[0]
        if n >= 3:
            return f"строка повторяется {n} раз"
    w = re.findall(r"\w+", t.lower())
    if len(w) >= 36:
        from collections import Counter

        grams = Counter(tuple(w[i:i + 6]) for i in range(len(w) - 5))
        g, n = grams.most_common(1)[0]
        rep = sum(c - 1 for c in grams.values() if c >= 3)
        if n >= 3 and rep / max(1, len(w) - 5) >= 0.3:
            return f"повтор фразы «{' '.join(g)}» ×{n}"
    return None


def similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, (a or "")[:2000], (b or "")[:2000]).ratio()


def failure_reason(e: BaseException) -> str:
    code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if code:
        return f"HTTP {code}"
    if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__:
        return f"таймаут ({e})" if str(e) else "таймаут"
    return type(e).__name__


class Llm:
    def __init__(self, s: Settings):
        self.s = s
        self._clients: dict = {}
        self.usage = Usage()
        self._lock = threading.Lock()
        self._client = None  # optional injected TokenRouter client (tests)
        self._last: dict = {}  # (conversation, step) -> (prompt hash, answer): a loop across consecutive calls
        self._dead: dict = {}  # provider -> until (time.time()): key rejected (401/403), treated as absent for a while
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
        return bool(self.s.api_key() or self.s.cloudru_api_key)

    def _provider(self, name: str) -> tuple[str, str, str]:
        if name == "cloudru":
            return self.s.cloudru_base_url, self.s.cloudru_api_key, self.s.cloudru_model
        if name == "quality":
            return self.s.llm_quality_url, self.s.api_key() or "local", self.s.llm_quality_model
        return self.s.llm_base_url, self.s.api_key(), self.s.llm_model

    def order(self, country: str | None = None) -> list[str]:
        """Providers to try, primary first. Only those with a key."""
        now = time.time()
        have = [n for n in ("cloudru", "tokenrouter") if self._provider(n)[1] and self._dead.get(n, 0) < now]
        prim = (REQUEST_PRIMARY.get() or self.s.llm_primary or "auto").lower()
        prim = "tokenrouter" if prim == "local" else prim  # LLM_PRIMARY=local: the OpenAI-compatible slot points at LOCAL_LLM_URL (docs/LOCAL_GPU.md)
        if prim == "auto":
            c = (country if country is not None else REQUEST_COUNTRY.get()) or ""
            ru = {x.strip().upper() for x in self.s.cloudru_countries.split(",") if x.strip()}
            prim = "cloudru" if (not c or c.upper() in ru) else "tokenrouter"
        out = sorted(have, key=lambda n: n != prim)
        if REQUEST_FORCE_FALLBACK.get() and len(out) > 1:
            out = out[1:] + out[:1]
        return out

    def _cli(self, prov: str = "tokenrouter"):
        if prov == "tokenrouter" and self._client is not None:  # injected client (tests)
            return self._client
        if prov not in self._clients:
            base, key, _ = self._provider(prov)
            if not key:
                raise LlmUnavailable("no api key")
            from openai import OpenAI

            c = OpenAI(base_url=base, api_key=key, timeout=self.s.llm_timeout_s, max_retries=1)
            self._clients[prov] = self._opik(c) if self._opik else c
        return self._clients[prov]

    def _create(self, kw: dict, prov: str = "tokenrouter") -> Any:
        """Hedged request: if the free model has not answered in `llm_hedge_s`, send the same request again
        and take whichever finishes first (cuts the long tail of provider latency; the loser is ignored)."""
        hedge = self.s.llm_hedge_s
        if hedge <= 0:
            return self._cli(prov).chat.completions.create(**kw)
        import concurrent.futures as cf

        if self._pool is None:
            self._pool = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="llm")
        futs = [self._pool.submit(self._cli(prov).chat.completions.create, **kw)]
        done, _ = cf.wait(futs, timeout=hedge)
        if not done:
            futs.append(self._pool.submit(self._cli(prov).chat.completions.create, **kw))
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
    def _run_stream(self, kw: dict, att: "_Attempt", on_text, is_winner, prov: str = "tokenrouter") -> None:
        """Consume one streamed completion into a message object (content + tool calls)."""
        try:
            stream = self._cli(prov).chat.completions.create(**kw, stream=True, stream_options={"include_usage": True})
            content: list[str] = []
            calls: dict[int, dict] = {}
            usage = None
            for ch in stream:
                att.last = time.time()
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
                if got or getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None):  # reasoning text is dropped
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

    def _race(self, kw: dict, on_text=None, prov: str = "tokenrouter", limit: float | None = None, first: float | None = None) -> Any:
        """Streamed request with a time-to-first-token hedge: if no token arrived in `llm_hedge_ttft_s`
        (the free model sometimes sits in a queue), a second identical request is sent; the first one that
        starts streaming wins and the other is cancelled. Output length no longer triggers useless duplicates."""
        import concurrent.futures as cf

        if self._pool is None:
            self._pool = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="llm")
        hedge = max(0.5, self.s.llm_hedge_ttft_s)
        limit = self.s.llm_timeout_s if limit is None else limit
        stall = self.s.llm_stall_s
        atts: list[_Attempt] = []
        win: list[_Attempt | None] = [None]

        def start() -> None:
            a = _Attempt()
            atts.append(a)
            self._pool.submit(self._run_stream, kw, a, on_text, lambda x: win[0] is x, prov)

        t0 = time.time()
        start()
        while True:
            ok = [a for a in atts if a.first.is_set() and a.error is None]
            if ok:
                w = win[0] = ok[0]
                for a in atts:
                    if a is not w:
                        a.cancel = True
                while not w.done.wait(0.1):
                    if time.time() - t0 > limit:
                        w.cancel = True
                        raise TimeoutError("llm stream timeout")
                    if stall > 0 and time.time() - w.last > stall:  # started, then went silent (seen: 107 s answers at night)
                        w.cancel = True
                        raise TimeoutError("llm stream stalled")
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
            if el > limit or (first is not None and el > first):
                for a in atts:
                    a.cancel = True
                raise TimeoutError(f"нет первого токена за {int(min(limit, first or limit))} с")
            time.sleep(0.05)

    def _account(self, step: str, r: Any, dt: float, prov: str = "") -> int:
        u = getattr(r, "usage", None)
        pt, ct = (u.prompt_tokens or 0, u.completion_tokens or 0) if u else (0, 0)
        cached = cached_tokens(u)
        with self._lock:
            self.usage.calls += 1
            self.usage.prompt_tokens += pt
            self.usage.completion_tokens += ct
            self.usage.seconds += dt
            self.usage.per_step.append({"step": step, "provider": prov, "prompt_tokens": pt, "completion_tokens": ct,
                                   "cached_tokens": cached, "seconds": round(dt, 2)})
        return pt + ct

    def chat(self, step: str, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 1800,
             temperature: float = 0.1, model: str | None = None, on_text=None) -> tuple[Any, int]:
        """Return (assistant message, tokens used).

        152-FZ: with masking on (default) the provider gets the messages with names, phones, plates, VINs, document numbers
        replaced by «[ФИО_1]»-style placeholders (pii.Masker, local regex, a few ms); the answer, every streamed chunk and the
        tool-call arguments get the real values back here, so neither the model provider nor its logs see them."""
        t = time.time()
        on = REQUEST_PII.get()
        mk = pii.Masker() if (self.s.pii_masking if on is None else on) else None
        if mk is not None:
            messages = mk.mask_messages(messages)
            if on_text is not None:
                _cb = on_text
                on_text = lambda txt: _cb(mk.unmask_partial(txt))  # noqa: E731 -- never half a placeholder on screen
        order = self.order()
        if not order:
            raise LlmUnavailable("no api key")
        if model and model != self.s.cloudru_model:  # explicit model (e.g. vision) lives at TokenRouter
            order = [p for p in order if p == "tokenrouter"] or order
        elif self.s.llm_quality_url and step in {x.strip() for x in self.s.llm_quality_steps.split(",") if x.strip()}:
            order = ["quality"] + order  # Spark: «Подробнее» on the 27B server, the primary stays free for questions
        dl = REQUEST_DEADLINE.get()
        left = (lambda: dl - time.time()) if dl else (lambda: self.s.llm_timeout_s)
        if left() < 3:
            raise LlmUnavailable("turn budget")
        last: Exception | None = None
        conv = REQUEST_CONV.get()
        prompt_key = hash(str(messages[-1].get("content"))[:4000]) if messages else 0
        reserve_dead = self._dead.get("cloudru", 0) > time.time()
        reserve_missing = (self.s.llm_fallback and "cloudru" not in order and order[0] == "tokenrouter" and (not self.s.cloudru_api_key or reserve_dead))
        missing_note = ("резерв GigaChat-4 отключён на 10 мин: Cloud.ru не принял ключ" if reserve_dead
                        else "CLOUDRU_API_KEY не задан — резерв GigaChat-4 пропущен")
        for i, prov in enumerate(order):
            has_next = self.s.llm_fallback and i < len(order) - 1
            nxt = order[i + 1] if i < len(order) - 1 else None
            kw: dict[str, Any] = dict(model=(model if prov == "tokenrouter" and model else self._provider(prov)[2]),
                                      messages=messages, temperature=temperature, max_tokens=max_tokens)
            if tools:
                kw["tools"] = tools
            if prov == "tokenrouter" and self.s.llm_thinking.lower() in ("off", "0", "false", "disabled"):
                kw["extra_body"] = {"thinking": {"type": "disabled"}}
            switched = False
            for attempt in range(1 if has_next else 2):  # with a reserve: one attempt, then the reserve (latency cap)
                lim = min(self.s.llm_timeout_s, left())
                if lim < 3 or (attempt and lim < 8):  # no time for a useful retry: the caller answers without the model
                    raise LlmUnavailable(type(last).__name__ if last else "turn budget") from None
                first = self.s.llm_primary_timeout_s if (has_next and self.s.llm_primary_timeout_s > 0) else None
                try:
                    r = (self._race(kw, on_text, prov, lim, first) if self.s.llm_stream
                         else self._create({**kw, "timeout": min(lim, first or lim)}, prov))
                    tok = self._account(step, r, time.time() - t, prov)
                    msg = r.choices[0].message
                    if getattr(msg, "content", None):
                        msg.content = re.sub(r"(?s)^\s*<think>.*?</think>\s*", "", msg.content)  # never show reasoning
                    if mk is not None:
                        if getattr(msg, "content", None):
                            msg.content = mk.unmask(msg.content)
                        for tc in getattr(msg, "tool_calls", None) or []:
                            fn = getattr(tc, "function", None)
                            if fn is not None and getattr(fn, "arguments", None):
                                fn.arguments = mk.unmask(fn.arguments)
                    bad = self._degenerate(conv, step, prompt_key, msg)
                    if bad and has_next:
                        llm_event(tool="llm_fallback", step=step, **{"from": prov}, to=nxt, model=self._provider(nxt)[2], reason=bad)
                        last, switched = RuntimeError(bad), True
                        break
                    if bad and reserve_missing:
                        llm_event(tool="llm_fallback", step=step, **{"from": prov}, skipped=True, reason=bad,
                                  note=missing_note)
                    if conv and getattr(msg, "content", None):
                        self._last[(conv, step)] = (prompt_key, msg.content)
                        if len(self._last) > 5000:
                            self._last.clear()
                    if i > 0 or REQUEST_FORCE_FALLBACK.get():
                        llm_event(tool="llm_answered", step=step, provider=prov, model=self._provider(prov)[2])
                    return msg, tok
                except LlmUnavailable:
                    break
                except Exception as e:  # network, rate limit, 5xx, stall: never leak the key
                    last = e
                    code = getattr(e, "status_code", None)
                    if code in (401, 403) and len(order) > 1:
                        self._dead[prov] = time.time() + 600  # rejected key: do not cost the primary its second attempt for 10 min
                        llm_event(tool="llm_fallback", step=step, **{"from": prov}, skipped=True, reason=failure_reason(e),
                                  note=f"{PROV_RU.get(prov, prov)}: ключ не принят, резерв отключён на 10 мин")
                        break  # an auth error does not go away on retry
                    if has_next:
                        llm_event(tool="llm_fallback", step=step, **{"from": prov}, to=nxt, model=self._provider(nxt)[2], reason=failure_reason(e))
                        switched = True
                        break
                    if reserve_missing and attempt == 0:
                        llm_event(tool="llm_fallback", step=step, **{"from": prov}, skipped=True, reason=failure_reason(e),
                                  note=missing_note)
                    time.sleep(min(1.5 * (attempt + 1), max(0.0, left() - 3)))
            if not switched and last is None:
                continue
        raise LlmUnavailable(type(last).__name__ if last else "no provider") from None

    def _degenerate(self, conv: str | None, step: str, prompt_key: int, msg: Any) -> str | None:
        """Why this answer is unusable (empty / a loop), or None."""
        text = getattr(msg, "content", None) or ""
        if not text.strip() and not (getattr(msg, "tool_calls", None) or []):
            return "пустой ответ"
        bad = degenerate_text(text)
        if bad:
            return bad
        prev = self._last.get((conv, step)) if conv else None
        if prev and prev[0] != prompt_key and len(text) >= 80:
            sim = similar(prev[1], text)
            if sim >= self.s.llm_repeat_similarity:
                return f"ответ почти совпадает с предыдущим на другой вопрос (сходство {sim:.2f})"
        return None

    def json(self, step: str, system: str, user: str, max_tokens: int = 2000, on_text=None) -> dict:
        msg, _ = self.chat(step, [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=max_tokens,
                           temperature=0, on_text=on_text)
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
