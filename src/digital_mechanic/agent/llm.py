"""OpenAI-compatible LLM client: tool calling, vision, token accounting, optional Opik tracing.

The API key is read from settings (env or key file) and is never logged or returned.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings


class LlmUnavailable(Exception):
    pass


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
             temperature: float = 0.1, model: str | None = None) -> tuple[Any, int]:
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
                r = self._cli().chat.completions.create(**kw)
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
