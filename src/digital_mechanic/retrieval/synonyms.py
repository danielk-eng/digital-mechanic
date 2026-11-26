"""Item 17: colloquial names of components and typos -> terms of the manuals ("did you mean").

Synonyms are a small hand list of workshop slang. Typos are corrected only towards words that occur in the
manuals of THIS engine (closest word by edit similarity), so a correction never invents a term.
"""

from __future__ import annotations

import difflib
import os
import re
from collections import Counter

from .bm25 import STOP, _WORD, stem

SLANG = {
    "гбц": "головка блока цилиндров", "тнвд": "топливный насос высокого давления",
    "турбина": "турбокомпрессор", "турбо": "турбокомпрессор", "антифриз": "охлаждающая жидкость", "тосол": "охлаждающая жидкость",
    "помпа": "водяной насос", "движок": "двигатель", "мотор": "двигатель", "маслонасос": "масляный насос", "генка": "генератор",
    "грм": "газораспределительный механизм", "распредвал": "распределительный вал", "коленвал": "коленчатый вал",
    "инжектор": "форсунка", "форсы": "форсунки", "свечи накала": "свечи накаливания", "масложор": "расход масла на угар",
    "троит": "неравномерная работа цилиндров", "дымит": "дымление", "клапана": "клапаны", "стучит": "стук", "гидрики": "гидротолкатели",
    "гидрокомпенсаторы": "гидротолкатели", "сцепа": "сцепление", "кпп": "коробка передач", "радик": "радиатор", "бендикс": "привод стартера",
}


class Normalizer:
    def __init__(self, pages):
        surf: dict[str, Counter] = {}
        for p in pages:
            for w in _WORD.findall(p.text or ""):
                lw = w.lower()
                if len(lw) < 4 or lw in STOP or lw[0].isdigit():
                    continue
                surf.setdefault(stem(lw), Counter())[lw] += 1
        self.surface = {s: c.most_common(1)[0][0] for s, c in surf.items() if sum(c.values()) >= 2}
        self.vocab = list(self.surface)

    def normalize(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        """Return (text with corrections, [(original, replacement)])."""
        changes: list[tuple[str, str]] = []
        out = text
        low = text.lower()
        for k, v in sorted(SLANG.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", low):
                out = re.sub(rf"(?i)(?<![\w-]){re.escape(k)}(?![\w-])", v, out)
                low = out.lower()
                changes.append((k, v))
        for w in set(_WORD.findall(out)):
            lw = w.lower()
            if len(lw) < 6 or lw in STOP or not re.fullmatch(r"[а-яё]+", lw) or stem(lw) in self.surface:
                continue
            st = stem(lw)
            m = difflib.get_close_matches(st, self.vocab, n=1, cutoff=0.8)
            if m and (m[0][:2] != st[:2] or len(os.path.commonprefix([m[0], st])) >= 5 or abs(len(m[0]) - len(st)) > 1):
                m = None  # different start, same root (just another form) or too different in length -> not a typo
            if m:
                rep = self.surface[m[0]]
                if rep != lw:
                    out = re.sub(rf"(?<![\w-]){re.escape(w)}(?![\w-])", rep, out)
                    changes.append((w, rep))
        return out, changes
