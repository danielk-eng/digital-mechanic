"""Page-level BM25 with Russian Snowball stemming and bigram bonus.

The engine filter is applied BEFORE ranking: an index is built per engine model from
pages of that engine's manuals only, so a page of another engine can never be returned.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

try:
    import snowballstemmer

    _STEM = snowballstemmer.stemmer("russian")
except Exception:  # pragma: no cover
    _STEM = None

from ..ingest.pages import ManualPage
from .filter import for_engine

_WORD = re.compile(r"[0-9]+(?:[.,][0-9]+)?|[a-zа-яё]+", re.I)
STOP = set(
    "и в во не на с со по к у о об от до за из для при как что это или а но же бы ли то так его ее её их он она они "
    "ряд какой какая какие каков какого мне нужно можно ли ли есть быть если чем где когда "
    "почему зачем отчего что делать сделать сегодня подскажи скажи пожалуйста".split()
)

# Verb / adjective forms whose Snowball stem differs from the noun used in the manuals ("стучит" -> "стук").
STEM_ALIAS = {
    "стуч": "стук", "стучат": "стук", "постукиван": "стук", "шумн": "шум", "вибрир": "вибрац", "перегрева": "перегр",
    "перегрет": "перегр", "дымлен": "дым", "дымн": "дым", "пуска": "пуск", "запуска": "запуск", "гуд": "гул",
    "подтека": "подтекан", "теч": "течь", "троен": "тро", "свистя": "свист",
}


_STEM_LOCK = threading.Lock()


@lru_cache(maxsize=200_000)
def stem(w: str) -> str:
    w = w.lower().replace("ё", "е")
    if _STEM and not w[0].isdigit():
        with _STEM_LOCK:  # the Snowball stemmer keeps state in the object: not thread-safe
            st = _STEM.stemWord(w)
        return STEM_ALIAS.get(st, st)
    return w


def tokens(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text) if w.lower() not in STOP and (len(w) > 1 or w.isdigit())]


@dataclass
class Hit:
    page: ManualPage
    score: float

    def as_dict(self) -> dict:
        return {"document": self.page.document, "doc_id": self.page.doc_id, "page": self.page.page, "score": round(self.score, 3)}


class Bm25:
    def __init__(self, pages: Sequence, text_of=lambda p: p.text, k1: float = 1.3, b: float = 0.7):
        self.pages, self.k1, self.b = list(pages), k1, b
        toks = [tokens(text_of(p)) for p in self.pages]
        self.tf = [Counter(t) for t in toks]
        self.bi = [set(zip(t, t[1:])) for t in toks]
        self.len = [len(t) for t in toks]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = max(len(self.pages), 1)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        q = list(dict.fromkeys(tokens(query)))
        qb = set(zip(q, q[1:]))
        out = []
        for i in range(len(self.pages)):
            s = 0.0
            tf = self.tf[i]
            matched = 0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    matched += 1
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s <= 0:
                continue
            if qb:
                s += 1.5 * len(qb & self.bi[i])
            if q:
                s *= 0.6 + 0.4 * matched / len(q)  # coverage of query terms
            out.append((i, s))
        out.sort(key=lambda x: -x[1])
        return out[:k]


class ManualIndex:
    def __init__(self, pages: list[ManualPage]):
        self.pages = pages
        self._by_engine: dict[str, Bm25] = {}
        self._pages_by_key = {(p.doc_id, p.page): p for p in pages}

    def engines(self) -> list[str]:
        return sorted({e for p in self.pages for e in p.engine_types})

    def _engine_index(self, engine: str) -> Bm25:
        if engine not in self._by_engine:
            self._by_engine[engine] = Bm25(for_engine(self.pages, engine))
        return self._by_engine[engine]

    def search(self, engine_type: str, query: str, k: int = 6) -> list[Hit]:
        idx = self._engine_index(engine_type)
        return [Hit(idx.pages[i], s) for i, s in idx.search(query, k)]

    def search_multi(self, engine_type: str, queries: list[str], k: int = 6, boost=None, depth: int = 30) -> list[Hit]:
        """Reciprocal rank fusion of several reformulations (original + symptom expansion + fault-table query).

        `boost(page) -> float` multiplies the fused score (e.g. fault tables for symptom questions)."""
        idx = self._engine_index(engine_type)
        fused: dict[int, float] = {}
        best: dict[int, float] = {}
        for qi, q in enumerate(q for q in queries if q and q.strip()):
            w = (1.0, 0.9, 0.5)[min(qi, 2)]
            for rank, (i, s) in enumerate(idx.search(q, depth)):
                fused[i] = fused.get(i, 0.0) + w / (60 + rank)
                best[i] = max(best.get(i, 0.0), s)
        if boost:
            fused = {i: v * boost(idx.pages[i]) for i, v in fused.items()}
        order = sorted(fused, key=lambda i: -fused[i])[:k]
        return [Hit(idx.pages[i], best[i]) for i in order]

    def get_page(self, doc_id: str, page: int, engine_type: str | None = None) -> ManualPage | None:
        p = self._pages_by_key.get((doc_id, page))
        if p and engine_type and engine_type not in p.engine_types:
            return None
        return p


@dataclass(frozen=True)
class ExtraPage:
    """Page of the supplementary truck manuals DB (not part of the customer's knowledge base)."""

    doc_id: str
    document: str
    make: str
    models: tuple[str, ...]
    source: str
    page: int
    text: str
    image: str | None = None


class ExtraIndex:
    def __init__(self, pages: list[ExtraPage]):
        self.pages = pages
        self._by_make: dict[str, Bm25] = {}

    def makes(self) -> set[str]:
        return {p.make for p in self.pages}

    def search(self, make: str, query: str, k: int = 4) -> list[tuple[ExtraPage, float]]:
        if make not in self._by_make:
            self._by_make[make] = Bm25([p for p in self.pages if p.make == make])
        idx = self._by_make[make]
        return [(idx.pages[i], s) for i, s in idx.search(query, k)]

    def get_page(self, doc_id: str, page: int) -> ExtraPage | None:
        for p in self.pages:
            if p.doc_id == doc_id and p.page == page:
                return p
        return None


# Sources excluded by the owner (unverified redistribution rights), never loaded even if present in a file.
EXCLUDED_EXTRA_SOURCES = {"avtoalfa.com"}
EXCLUDED_EXTRA_DOCS = {"EXTRA-KAMAZ4350-RE"}


def load_extra(path: str) -> list[ExtraPage]:
    import gzip, json
    from pathlib import Path

    if not Path(path).exists():
        return []
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("doc_id") in EXCLUDED_EXTRA_DOCS or r.get("source") in EXCLUDED_EXTRA_SOURCES:
                continue
            r["models"] = tuple(r.get("models") or ())
            out.append(ExtraPage(**r))
    return out


def snippet(text: str, query: str, max_chars: int = 1800) -> str:
    """Window of the page around the lines that best match the query (page text kept for verification)."""
    if len(text) <= max_chars:
        return text
    q = set(tokens(query))
    lines = text.split("\n")
    scores = [len(q & set(tokens(l))) for l in lines]
    best = max(range(len(lines)), key=lambda i: sum(scores[max(0, i - 3): i + 4]))
    lo, hi = best, best
    size = len(lines[best])
    while size < max_chars and (lo > 0 or hi < len(lines) - 1):
        if hi < len(lines) - 1:
            hi += 1; size += len(lines[hi]) + 1
        if lo > 0 and size < max_chars:
            lo -= 1; size += len(lines[lo]) + 1
    return "\n".join(lines[lo: hi + 1])[: max_chars + 200]
