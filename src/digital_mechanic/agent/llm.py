"""Thin OpenAI-compatible LLM client with token accounting. The key is never logged."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from ..config import Settings


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    per_step: list[dict] = field(default_factory=list)


class LlmUnavailable(RuntimeError):
    pass


class Llm:
    def __init__(self, settings: Settings):
        self.s = settings
        self.usage = Usage()
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.s.llm_api_key and self.s.llm_base_url and self.s.llm_model)

    def _cli(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.s.llm_base_url, api_key=self.s.llm_api_key, timeout=self.s.llm_timeout_s, max_retries=2)
        return self._client

    def json(self, step: str, system: str, user: str, max_tokens: int = 1500, attempt: int = 0) -> dict:
        if not self.enabled:
            raise LlmUnavailable("LLM is not configured")
        t = time.time()
        try:
            r = self._cli().chat.completions.create(
                model=self.s.llm_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=max_tokens,
            )
        except Exception as e:  # network, auth, rate limit: never leak the key
            raise LlmUnavailable(type(e).__name__) from None
        dt = time.time() - t
        u = getattr(r, "usage", None)
        pt, ct = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
        self.usage.calls += 1
        self.usage.prompt_tokens += pt
        self.usage.completion_tokens += ct
        self.usage.seconds += dt
        self.usage.per_step.append({"step": step, "prompt_tokens": pt, "completion_tokens": ct, "seconds": round(dt, 2)})
        text = r.choices[0].message.content or ""
        try:
            return parse_json(text)
        except LlmUnavailable:
            if attempt >= 1:
                raise
            return self.json(step + "_retry", system, user, max_tokens=max_tokens * 2, attempt=attempt + 1)


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LlmUnavailable("no json in answer")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        raise LlmUnavailable("bad json") from None
