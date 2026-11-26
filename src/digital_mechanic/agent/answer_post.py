"""Answer post-processing: no trailing «Источник» block (pages are inline chips in the UI) and no
offer-questions at the end («Создать черновик заявки?»). Offers become structured action buttons instead."""
from __future__ import annotations

import re

_LBL = re.compile(r"\[[^\]]*\b[SX]\d+\b[^\]]*\]")
_SRC_HEAD = re.compile(r"^\s*(?:[-•*]\s*)?(?:\*\*)?\s*Источник(?:и)?\s*(?:\*\*)?\s*[:：—-]?", re.I)
_HEAD = re.compile(r"^\s*\*\*[^*]{2,40}\*\*")
_OFFER_START = re.compile(r"^(?:создать|создаём|создадим|оформить|оформляем|сформировать|подготовить|начать|начинаем|запустить|показать|открыть|"
                          r"передать|перейти|продолжить|отметить|сделать|хотите|хочешь|нужн\w*|надо ли|если (?:нужно|надо|хотите|хочешь)|могу|"
                          r"скажите|скажи|напишите|давайте|готов\w*|при необходимости)\b", re.I)
_OFFER_ANY = re.compile(r"(черновик|заявк|дефектовк|ремонт\w* по шагам|следующ\w+ шаг|раздел|главному|подготовлю|создам|оформлю|начну|покажу|открою|передам)", re.I)
_SENT = re.compile(r"(?<=[.!?…])\s+")

INTENTS = (("ticket", re.compile(r"заявк|дефектовк|черновик", re.I)), ("repair", re.compile(r"ремонт|шаг", re.I)),
           ("handoff", re.compile(r"главному|передать", re.I)), ("section", re.compile(r"раздел|страниц|руководств", re.I)))


def _plain(s: str) -> str:
    return re.sub(r"[*_#>]", "", _LBL.sub("", s)).strip(" \t-—•")


def is_offer(sentence: str) -> bool:
    p = _plain(sentence)
    if not p:
        return False
    if p.endswith("?"):
        return bool(_OFFER_START.match(p) or _OFFER_ANY.search(p))
    # «Скажите — подготовлю черновик.» / «Если нужно, начну ремонт по шагам.»
    return bool(_OFFER_START.match(p) and re.search(r"подготовлю|создам|оформлю|начну|покажу|открою|передам|скажите|скажи", p, re.I))


def strip_sources(text: str) -> str:
    out, skip = [], False
    for line in text.split("\n"):
        if _SRC_HEAD.match(line):
            skip = True  # the heading line and its list items
            continue
        if skip:
            if not line.strip() or (_HEAD.match(line) and not _SRC_HEAD.match(line)):
                skip = False
            elif re.match(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+", line) or _LBL.search(line) and len(_plain(line)) < 80:
                continue
            else:
                skip = False
        out.append(line)
    return "\n".join(out)


def strip_offers(text: str) -> tuple[str, list[str]]:
    lines = text.rstrip().split("\n")
    offers: list[str] = []
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        sents = _SENT.split(last)
        cut = len(sents)
        while cut > 0 and is_offer(sents[cut - 1]):
            cut -= 1
        if cut == len(sents):
            break
        offers += sents[cut:]
        if cut == 0:
            lines.pop()
        else:
            lines[-1] = " ".join(sents[:cut])
            break
    return "\n".join(lines).rstrip(), offers


def clean(text: str) -> tuple[str, set[str]]:
    """-> (text without the «Источник» block and trailing offers, intents named by the stripped offers)."""
    if not text:
        return text, set()
    t = strip_sources(text)
    t, offers = strip_offers(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    intents = {name for o in offers for name, rx in INTENTS if rx.search(o)}
    return (t or text), intents
