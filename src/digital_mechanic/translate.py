"""Content translation into the interface language (manuals, tickets, chips, history) until the databases are
translated by people: imported human translation -> cache -> LLM on the fly.

Grounding rule: numbers, codes, part numbers, units and page refs must survive. A translation that loses or
changes any number is rejected and the Russian original is shown instead (the user can always see the original).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
import time
from collections import Counter
from pathlib import Path

import httpx

from . import pii
from .agent.llm import LlmUnavailable, parse_json
from .config import get_settings
from .i18n import LANGS

CYR = re.compile(r"[А-Яа-яЁё]")
# letters a Russian text never has; their presence means the text is already in that language
OWN_LETTERS = {"kk": "әғқңөұүһі", "ky": "ңөү", "tg": "ғӣқӯҳҷ", "be": "ўі", "uz": "ўқғҳ"}
NUM = re.compile(r"\d+(?:[.,]\d+)?")
TOKENS = re.compile(r"\[(?:S|X)\d+\]|\b[A-Z]{1,4}-?\d[\w.-]*|\b[А-ЯЁ]{2,5}-\d[\w.-]*|\bF\d{2}\b")  # [S1], ЯМЗ-236 style codes, F08

SYSTEM = """Ты переводчик технической документации для механиков грузовых автомобилей.
Переведи каждый элемент массива items с русского. Язык перевода: {name} — пиши на {lang} языке. Регистр: живой язык мастерской, без канцелярита, термины как говорят механики.
Строго сохрани без изменений: все числа и единицы (мм, Н·м, кгс·см, бар, об/мин, °C, л), коды неисправностей (F01–F14), номера деталей и
документов (236-1003270, 236-3902150-Б РЭ), марки и модели в исходном написании, кириллицей (ЯМЗ-236М2, МАЗ-5337), ссылки на страницы («с. 52»), метки в квадратных скобках ([S1]),
разметку (**, •, переводы строк). Ничего не добавляй и не сокращай. Если элемент уже не на русском — верни как есть.
{glossary}Верни JSON: {{"items": ["перевод 1", "перевод 2", ...]}} — столько же элементов и в том же порядке."""


def looks_russian(text: str, lang: str) -> bool:
    if len(CYR.findall(text or "")) < 2 or not re.search(r"[а-яё]{3,}", text or ""):
        return False  # model names (МАЗ-5337, ЯМЗ-236М2) and abbreviations stay as they are
    own = OWN_LETTERS.get(lang)
    return not (own and any(ch in own for ch in text.lower()))


def numbers_kept(src: str, out: str) -> bool:
    norm = lambda s: Counter(x.replace(",", ".") for x in NUM.findall(s))  # noqa: E731
    a, b = norm(src), norm(out)
    return (all(b[k] >= v for k, v in a.items()) and all(k in a for k in b)  # nothing lost, nothing invented
            and all(t in out for t in TOKENS.findall(src)))


def _h(lang: str, text: str) -> str:
    return hashlib.sha1(f"{lang}\n{text}".encode()).hexdigest()


class NvidiaBatchTranslator:
    """OpenAI-compatible NVIDIA Build chat model used as the first batch translation engine.

    The specialised Riva translation endpoint covers only a subset of the app's languages, so the configurable
    instruction-following model is used for the same JSON batch contract as the main LLM; formatting/glossary rules stay intact.
    """

    def __init__(self) -> None:
        self.s = get_settings()

    @property
    def available(self) -> bool:
        return bool(self.s.nvidia_translate_enabled and self.s.nvidia_key())

    def json(self, step: str, system: str, user: str, max_tokens: int = 2000, on_text=None) -> dict:
        key = self.s.nvidia_key()
        if not key:
            raise LlmUnavailable("no nvidia key")
        mk = pii.Masker() if self.s.pii_masking else None
        if mk:
            system, user = mk.mask(system), mk.mask(user)
        payload = {
            "model": self.s.nvidia_translate_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            r = httpx.post(f"{self.s.nvidia_base_url.rstrip('/')}/chat/completions",
                           headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                           json=payload, timeout=self.s.nvidia_translate_timeout_s)
            deadline = time.monotonic() + min(self.s.nvidia_translate_timeout_s, 55.0)
            while r.status_code == 202 and time.monotonic() < deadline:  # NVCF cold/queued invocation
                reqid = r.headers.get("NVCF-REQID") or r.headers.get("nvcf-reqid")
                if not reqid:
                    break
                time.sleep(0.7)
                r = httpx.get(f"https://api.nvcf.nvidia.com/v2/nvcf/pexec/status/{reqid}",
                              headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=10)
        except httpx.HTTPError as e:
            raise LlmUnavailable(f"nvidia translate network: {type(e).__name__}") from None
        if r.status_code != 200:
            raise LlmUnavailable(f"nvidia translate {r.status_code}")
        try:
            content = r.json()["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, ValueError):
            raise LlmUnavailable("nvidia translate bad response") from None
        res = parse_json(content)
        if not mk:
            return res

        def restore(x):
            if isinstance(x, str):
                return mk.unmask(x)
            if isinstance(x, list):
                return [restore(v) for v in x]
            if isinstance(x, dict):
                return {k: restore(v) for k, v in x.items()}
            return x

        return restore(res)


class Translator:
    def __init__(self, llm, store, i18n_dir: str | Path = "data/i18n"):
        self.llm, self.store = llm, store
        self.nvidia = NvidiaBatchTranslator()
        self.dir = Path(i18n_dir)
        self.strings: dict[str, dict[str, str]] = {}  # lang -> {ru: tr} imported by people
        self.pages: dict[str, dict[tuple[str, int], str]] = {}  # lang -> {(doc_id, page): text}
        self.glossary: dict[str, dict[str, str]] = {}  # lang -> {ru term: tr}
        self._lock = threading.Lock()
        store.x("CREATE TABLE IF NOT EXISTS translations(h TEXT PRIMARY KEY, lang TEXT, src TEXT, out TEXT, ts REAL, engine TEXT)")
        self.load()

    def load(self) -> None:
        if not self.dir.is_dir():
            return
        for f in self.dir.glob("strings_*.json"):
            self.strings[f.stem.split("_", 1)[1]] = json.loads(f.read_text(encoding="utf-8"))
        for f in self.dir.glob("pages_*.jsonl.gz"):
            lang = f.name[len("pages_"):].split(".")[0]
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                self.pages[lang] = {(r["doc_id"], int(r["page"])): r["text"] for r in map(json.loads, fh)}
        g = self.dir / "glossary.json"
        if g.is_file():
            self.glossary = json.loads(g.read_text(encoding="utf-8"))

    def stats(self) -> dict:
        return {"strings": {k: len(v) for k, v in self.strings.items()}, "pages": {k: len(v) for k, v in self.pages.items()},
                "cached": (self.store.one("SELECT COUNT(*) n FROM translations") or {}).get("n", 0)}

    def _glossary_hint(self, lang: str, texts: list[str]) -> str:
        g = self.glossary.get(lang) or {}
        joined = " ".join(texts).lower()
        hit = [(k, v) for k, v in g.items() if v and k.lower() in joined][:40]
        return ("Глоссарий (используй именно эти термины):\n" + "\n".join(f"{k} = {v}" for k, v in hit) + "\n") if hit else ""

    def translate(self, texts: list[str], lang: str, refs: list | None = None) -> list[dict]:
        """-> [{"text": translated or original, "src": "imported|page|cache|llm|same|failed"}] in the same order."""
        out: list[dict | None] = [None] * len(texts)
        todo: list[int] = []
        for i, t in enumerate(texts):
            t = t or ""
            ref = (refs or [None] * len(texts))[i] if refs else None
            if lang not in LANGS or lang == "ru" or not looks_russian(t, lang):
                out[i] = {"text": t, "src": "same"}
            elif t.strip() in self.strings.get(lang, {}):
                out[i] = {"text": self.strings[lang][t.strip()], "src": "imported"}
            elif ref and (ref.get("doc_id"), int(ref.get("page") or 0)) in self.pages.get(lang, {}):
                out[i] = {"text": self.pages[lang][(ref["doc_id"], int(ref["page"]))], "src": "imported"}
            else:
                row = self.store.one("SELECT out FROM translations WHERE h=?", (_h(lang, t),))
                if row:
                    out[i] = {"text": row["out"], "src": "cache"}
                else:
                    todo.append(i)
        batches, cur, size = [], [], 0
        for i in todo:  # batches of ~3500 characters, translated in parallel
            if cur and size + len(texts[i]) > 3500:
                batches.append(cur); cur, size = [], 0
            cur.append(i); size += len(texts[i])
        if cur:
            batches.append(cur)
        if batches:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(min(4, len(batches))) as ex:
                list(ex.map(lambda b: self._llm_batch([texts[j] for j in b], lang, b, out), batches))
        return [o or {"text": texts[k], "src": "failed"} for k, o in enumerate(out)]

    def _llm_batch(self, items: list[str], lang: str, idx: list[int], out: list) -> None:
        engines = []
        if getattr(self.nvidia, "available", False):
            engines.append(("nvidia", self.nvidia))
        if getattr(self.llm, "available", True):
            engines.append(("llm", self.llm))
        if not engines:
            return
        sys_ = SYSTEM.format(name=LANGS[lang]["name"], lang=LANGS[lang]["llm"], glossary=self._glossary_hint(lang, items))
        remaining = list(range(len(items)))
        for engine_name, engine in engines:
            if not remaining:
                break
            batch = [items[pos] for pos in remaining]
            try:
                res = engine.json("translate", sys_, json.dumps({"items": batch}, ensure_ascii=False),
                                  max_tokens=min(8000, 600 + 3 * sum(map(len, batch))))
                got = res.get("items") if isinstance(res, dict) else None
            except Exception:  # noqa: BLE001 - translation must never break the screen
                got = None
            if not isinstance(got, list) or len(got) != len(batch):
                continue
            retry = []
            for pos, src, tr in zip(remaining, batch, got):
                j = idx[pos]
                tr = str(tr or "")
                if tr and numbers_kept(src, tr):
                    out[j] = {"text": tr, "src": engine_name}
                    self.store.x("INSERT OR REPLACE INTO translations(h,lang,src,out,ts,engine) VALUES (?,?,?,?,?,?)",
                                 (_h(lang, src), lang, src[:4000], tr, time.time(), engine_name))
                else:
                    retry.append(pos)
            remaining = retry
