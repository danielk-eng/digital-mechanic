"""Pinned "key facts" block above an answer: danger / tools / torques / control values.

Taken VERBATIM from the manual pages the answer cites (never generated), so every number in the block is on a
page by construction ("no page = no number").
"""

from __future__ import annotations

import re

from ..retrieval.bm25 import tokens

KINDS = [
    ("danger", re.compile(r"внимание|предупрежд|запрещ|опасн|не допуска|осторожн|ожог|травм|во избежание", re.I)),
    ("torque", re.compile(r"\d\s*(?:Н\s?[·.∙•*]?\s?м|кгс\s?[·.∙•*]?\s?м)", re.I)),
    ("tool", re.compile(r"динамометрическ|ключ\w*\s+(?:на\s|S\s*=)|щуп|приспособлени|съ[её]мник|оправк|индикатор\w*\s|стетоскоп|компрессометр|манометр", re.I)),
    ("value", re.compile(r"\d[\d,.…\-–]*\s*(?:мм|МПа|бар|кгс/см|°\s?С|°C|об/мин|кПа)\b", re.I)),
]
LIMIT = {"danger": 1, "tool": 1, "torque": 2, "value": 2}


def sentences(text: str) -> list[str]:
    t = re.sub(r"-\n(?=[а-яё])", "", text)
    t = re.sub(r"\n\s*[-–•]\s+", "\n§", t)  # bullet items are separate statements
    t = re.sub(r"[ \t]*\n[ \t]*", " ", t)
    out = []
    for s in re.split(r"(?<=[.!?;])\s+(?=[А-ЯЁA-Z0-9§])|§", t):
        s = s.strip(" §")
        if len(s) >= 25:
            out.append(s)
    return out


def clip(s: str, rx, width: int = 230) -> str:
    """Long statement -> window around the matched fact, cut at word boundaries."""
    if len(s) <= width + 30:
        return s
    m = rx.search(s)
    mid = m.start() if m else 0
    lo = max(0, mid - width // 2)
    hi = min(len(s), lo + width)
    lo = s.rfind(" ", 0, lo) + 1 if lo else 0
    hi = s.find(" ", hi) if s.find(" ", hi) != -1 else len(s)
    return ("…" if lo else "") + s[lo:hi].strip() + ("…" if hi < len(s) else "")


def key_facts(frags: list[dict], query: str, answer: str, limit: int = 4) -> list[dict]:
    """frags: [{"label","doc_id","page","text"}] cited by the answer, in citation order."""
    qq = set(tokens(query))
    q = qq | set(tokens(answer))
    cands = []
    for rank, f in enumerate(frags[:4]):
        for s in sentences(f.get("text", "")):
            kr = next(((k, rx) for k, rx in KINDS if rx.search(s)), None)
            if not kr:
                continue
            kind, s = kr[0], clip(s, kr[1])
            if re.search(r"[А-ЯЁ]{4,}\s+[А-ЯЁ]{4,}\s+[А-ЯЁ]{4,}", s):
                continue  # table header run-in, not a statement
            st = set(tokens(s))
            ov = len(q & st)
            if ov < (1 if kind == "danger" else 2) or (qq and not qq & st):
                continue  # must touch the question itself, not only the answer wording
            cands.append((ov - 0.3 * rank, kind, s, f))
    cands.sort(key=lambda x: -x[0])
    out, used, seen = [], {}, set()
    for _, kind, s, f in cands:
        key = s[:60].lower()
        if used.get(kind, 0) >= LIMIT[kind] or key in seen:
            continue
        used[kind] = used.get(kind, 0) + 1
        seen.add(key)
        out.append({"kind": kind, "text": s, "label": f["label"], "doc_id": f["doc_id"], "page": f["page"]})
        if len(out) >= limit:
            break
    order = {"danger": 0, "tool": 1, "torque": 2, "value": 3}
    return sorted(out, key=lambda x: order[x["kind"]])
