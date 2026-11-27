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


# «код F08 по 236-3902150-Б РЭ, стр. PDF 52 относится к…» (expanded answer, Vercel 01:20): the internal document code and
# «стр. PDF» are index jargon; the page chip next to the label shows the page. Keep the sentence, drop the reference.
_CODE = r"\d{3}-\d{6,8}(?:-[А-ЯA-Z]\d?)?(?:\s*(?:РЭ|РК|ИЭ|РС))?"
_PG = r",\s*стр\.?\s*(?:PDF\s*)?\d+"
_DOC_REF = re.compile(rf"\s*(?:(?:по|в|из)\s+{_CODE}(?:{_PG})?|{_CODE}{_PG}),?(?=[\s.;:)])")


def strip_doc_refs(text: str) -> str:
    t = _DOC_REF.sub("", text)
    return re.sub(r"\bстр\.\s*PDF\s*", "стр. ", t)


def clean(text: str) -> tuple[str, set[str]]:
    """-> (text without the «Источник» block and trailing offers, intents named by the stripped offers)."""
    if not text:
        return text, set()
    t = strip_sources(text)
    t = strip_doc_refs(t)
    t, offers = strip_offers(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    intents = {name for o in offers for name, rx in INTENTS if rx.search(o)}
    return (t or text), intents


# ---------------------------------------------------------------- organizers 26.11: numbers only with a page, labelled hypotheses
_UNIT_NUM = re.compile(r"\d+(?:[.,]\d+)?(?:\s*(?:[-–…]|до)\s*\d+(?:[.,]\d+)?)?\s*(?:кгс\s*[·*.⋅]?\s*м|кгс\s*/\s*см|н\s*[·*.⋅]?\s*м|нм\b|кпа|мпа|бар\b|мм\b|°\s*c|ºс|°с|град|об\s*/\s*мин|мин[-−]1|л\b|литр)", re.I)
_LABEL_ANY = re.compile(r"\[(?:[^\]]*\b)?[SX]\d+\b[^\]]*\]")
_LONG_QUOTE = re.compile(r"«[^»]{25,}»")
_NOT_MANUAL = re.compile(r"(не из руководства|гипотез)", re.I)
_SPECIALIST = re.compile(r"[,;]?\s*(?:и\s+|или\s+)?(?:рекоменду\w+\s+)?(?:обрат\w+|проконсультир\w+)\s+(?:к|с|у)\s+специалист\w*(?:[^.\n]*)?", re.I)


def guard_uncited(text: str) -> tuple[str, list[str]]:
    """A number with a unit (norm, tolerance, torque, pressure) and no [S#]/[X#] label in its line, a number in a
    «Не из руководства» / «Гипотеза» line, or a long «quote» without a label -> the sentence is removed.
    -> (text, removed sentences)."""
    if not text:
        return text, []
    out, removed = [], []
    for line in text.split("\n"):
        labelled = bool(_LABEL_ANY.search(line))
        no_src = bool(_NOT_MANUAL.search(line))
        if (_UNIT_NUM.search(line) and (not labelled or no_src)) or (_LONG_QUOTE.search(line) and not labelled):
            sents = re.split(r"(?<=[.!?])\s+", line)
            keep = []
            for s in sents:
                bad = (_UNIT_NUM.search(s) and (no_src or not labelled)) or (_LONG_QUOTE.search(s) and not labelled)
                (removed if bad else keep).append(s)
            line = " ".join(keep)
            if not line.strip():
                continue
        out.append(line)
    return "\n".join(out), removed


def no_specialist(text: str) -> str:
    """«обратитесь к специалисту» -> a concrete action (the mechanic IS the specialist)."""
    if not text or "специалист" not in text.lower():
        return text
    def rep(m: re.Match) -> str:
        before = text[:m.start()].rstrip()
        if not before or before[-1] in ".!?\n:":
            return " Проведите дефектовку узла или откройте ближайший раздел руководства"
        return " — проведите дефектовку узла или откройте ближайший раздел руководства"
    return re.sub(r" {2,}", " ", _SPECIALIST.sub(rep, text)).strip()


_TICKET_CLAIM = re.compile(r"[^.!?\n]*(?:заявк\w*[^.!?\n]{0,40}можно оформ\w*|можно оформ\w*[^.!?\n]{0,20}заявк\w*|черновик[^.!?\n]{0,20}(?:уже )?готов)[^.!?\n]*[.!?]?")


def ticket_claims(text: str, why: str) -> tuple[str, bool]:
    """The model must not promise a ticket the policy holds back (0,50–0,60 / warning: inspection first; < 0,50,
    mismatch, bad: no ticket). Such sentences are replaced by the policy's own words."""
    if not text or not _TICKET_CLAIM.search(text):
        return text, False
    out = _TICKET_CLAIM.sub("", text).rstrip()
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return (out + "\n\n" + why).strip(), True
