"""Page-level BM25 with light Russian stemming. The engine filter is applied BEFORE ranking."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from digital_mechanic.ingest.pages import ManualPage
from .filter import for_engine

_WORD = re.compile(r"[0-9a-zа-яё]+", re.I)
STOP = set("и в во не на с со по к у о об от до за из для при как что это или а но же бы ли то так его ее их он она они ряд".split())


def stem(w: str) -> str:
    w = w.lower().replace("ё", "е")
    return w[:6] if len(w) > 6 else (w[:5] if len(w) > 5 else w)


def tokens(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text) if w.lower() not in STOP and len(w) > 1]


@dataclass
class Hit:
    page: ManualPage
    score: float

    def as_dict(self) -> dict:
        return {"document": self.page.document, "doc_id": self.page.doc_id, "page": self.page.page, "score": round(self.score, 3)}


class ManualIndex:
    def __init__(self, pages: list[ManualPage]):
        self.pages = pages
        self._by_engine: dict[str, "_Bm25"] = {}

    def _engine_index(self, engine: str) -> "_Bm25":
        if engine not in self._by_engine:
            self._by_engine[engine] = _Bm25(for_engine(self.pages, engine))
        return self._by_engine[engine]

    def search(self, engine_type: str, query: str, k: int = 6) -> list[Hit]:
        return self._engine_index(engine_type).search(query, k)

    def get_page(self, engine_type: str, doc_id: str, page: int) -> ManualPage | None:
        for p in for_engine(self.pages, engine_type):
            if p.doc_id == doc_id and p.page == page:
                return p
        return None


class _Bm25:
    def __init__(self, pages: list[ManualPage], k1: float = 1.4, b: float = 0.75):
        self.pages, self.k1, self.b = pages, k1, b
        self.tf = [Counter(tokens(p.text)) for p in pages]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(pages)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int) -> list[Hit]:
        q = tokens(query)
        out = []
        for i, p in enumerate(self.pages):
            s = 0.0
            for t in q:
                f = self.tf[i].get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                out.append(Hit(p, s))
        out.sort(key=lambda h: -h.score)
        return out[:k]
