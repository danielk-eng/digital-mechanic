"""Grounding checks: verbatim quotes and numbers must come from retrieved manual pages."""

from __future__ import annotations

import re

_DASH = r"[-‐‑–—]"


def norm(s: str) -> str:
    s = s.lower().replace("ё", "е").replace("\u00ad", "")
    s = re.sub(_DASH + r"\s*\n\s*", "", s)  # hyphenated line breaks
    s = re.sub(_DASH, "-", s)
    s = s.replace("…", "...").replace("⋅", "·").replace("*", "·")
    s = re.sub(r"[\s\u00a0]+", " ", s)
    s = re.sub(r"[«»\"'`“”„;:()\[\]]", "", s)
    return s.strip(" .,")


def quote_in_text(quote: str, text: str, min_len: int = 10) -> bool:
    q = norm(quote)
    if len(q) < min_len:
        return False
    t = norm(text)
    if q in t:
        return True
    # tolerate punctuation differences: compare letters/digits only
    qa = re.sub(r"[^0-9a-zа-я]", "", q)
    return len(qa) >= min_len and qa in re.sub(r"[^0-9a-zа-я]", "", t)


_NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?")


def numbers(text: str) -> set[str]:
    return {n.replace(",", ".").lstrip("0") or "0" for n in _NUM.findall(text or "")}


def unverified_numbers(answer: str, evidence: list[str], allowed: set[str] | None = None, free_max: int = 12) -> list[str]:
    """Numbers in the answer that appear in no evidence text (manual fragments, check data)."""
    clean = re.sub(r"\[[^\]]{0,80}\b(?:S|X)\d+\b[^\]]{0,80}\]", " ", answer)
    clean = re.sub(r"(?i)(стр\.?|page|pdf|bet|саҳ\.?|tr\.|पृ\.)\s*\d+", " ", clean)  # page refs are checked separately
    clean = re.sub(r"\b[FfSs]-?\d{2,4}\b|TKT-\d+", " ", clean)  # fault codes, session / ticket ids
    # engine / vehicle model designations: ЯМЗ-236М2, ЗМЗ-40904.10, КамАЗ-4350, 238М2
    clean = re.sub(r"[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z]{1,6}-\d[\w.]*|\b\d{3,5}[А-ЯЁA-Z]\d?\b", " ", clean)
    have: set[str] = set(allowed or set())
    for e in evidence:
        have |= numbers(e)
    bad = []
    for n in numbers(clean):
        try:
            v = float(n)
        except ValueError:
            continue
        if v.is_integer() and v <= free_max:
            continue  # step numbers, counts
        if n not in have:
            bad.append(n)
    return sorted(set(bad))


def cited_labels(answer: str) -> list[str]:
    """Labels inside square brackets: [S3], [S3, стр. 41], [S3; S5]."""
    out = []
    for grp in re.findall(r"\[([^\]]{1,80})\]", answer):
        out += re.findall(r"\b((?:S|X)\d+)\b", grp)
    return list(dict.fromkeys(out))
