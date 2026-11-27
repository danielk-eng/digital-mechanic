"""Which manual pages may back a ticket (organizers' answers in the task chat, 26.11, and the day-2 briefing).

* Applicable = a check / adjustment / repair procedure for the unit the fault type points to, from THIS engine's
  manual. A general inspection counts only if the page explicitly concerns that unit.
* Not evidence: a table of contents or a general maintenance section that does not mention the unit, another
  engine's manual (the S# labels are already this engine's only), a page that cannot be quoted verbatim
  (checked by quote_in_text before this module).
* Low oil pressure (F02): the page with the check by a control manometer and the pressure norm is required.
* A ticket is for a repair only (CTO, 27.11): checks/inspections alone are done on site, not ticketed.
* No applicable instruction -> no ticket; the mechanic sees why and gets a defect-check recommendation text.
"""
from __future__ import annotations

import re

from .grounding import norm

# Unit words per fault type (normalised, lower case, «е» for «ё»).
UNIT_TERMS: dict[str, tuple[str, ...]] = {
    "F01": ("клапан", "зазор", "коромысл", "толкател", "распределительн", "грм", "штанг"),
    "F02": ("масл",),
    "F03": ("охлажд", "термостат", "радиатор", "перегрев", "прокладк", "головк", "гбц", "жидкост", "насос водян", "водяного насоса", "водяной насос"),
    "F04": ("ремн", "ремен", "ремён", "натяжн", "шкив"),
    "F05": ("генератор",),
    "F06": ("сцеплен",),
    "F07": ("коробк", "передач", "кпп"),
    "F08": ("топлив", "форсунк", "тнвд", "насос высокого", "впрыск"),
    "F09": ("турбокомпрессор", "наддув", "турбин"),
    "F10": ("компрессор", "гидроусилит", "гур", "рулев"),
    "F11": ("рециркуляц", "egr", "отработавш"),
    "F12": ("зажиган", "свеч", "катушк", "распределител"),
    "F13": ("топлив", "бензин", "бензонасос", "форсунк", "дроссел", "карбюратор"),
    "F14": ("цеп", "натяжител", "успокоител", "грм", "распределительн"),
}

_TOC = re.compile(r"(?:\.\s?){6,}\s*\d{1,3}")
_TOC_HEAD = re.compile(r"^\s*(содержание|оглавление)\b", re.I)
_INSPECT = re.compile(r"^\s*(осмотр\w*|осмотреть|провер\w*|контрол\w*|проконтролир\w*|измер\w*|замер\w*|диагност\w*|убедит\w*|определ\w*|"
                      r"прослуша\w*|оцен\w*|выяв\w*)\b", re.I)
_REPAIR = re.compile(r"замен|регулир|отрегулир|подтян|затян|ремонт|восстанов|сня\w*|снима|установ|промы|очист|притер|шлиф|расточ|перебор|"
                     r"разбор|разобр|собр|сборк|устран|подкач|долить|долейте|слить|замени|натян|отрихт|прочист|подтягив", re.I)
_MANOMETER = re.compile(r"манометр", re.I)
_PRESSURE_NORM = re.compile(r"\d+(?:[.,]\d+)?\s*(?:[-–…]\s*\d+(?:[.,]\d+)?\s*)?(?:кпа|мпа|кгс\s*/\s*см|бар)", re.I)


def is_toc(text: str) -> bool:
    return bool(_TOC_HEAD.match(text or "")) or len(_TOC.findall(text or "")) >= 4


def concerns_unit(code: str | None, text: str) -> bool:
    terms = UNIT_TERMS.get(code or "")
    if not terms:
        return True
    t = norm(text)
    return any(w in t for w in terms)


def is_inspection_only(op_text: str, quote: str = "") -> bool:
    """«Осмотреть ремень» -> True; «Проверить тепловые зазоры» with the quote «…и подрегулировать…» -> False."""
    return bool(_INSPECT.match(op_text or "")) and not _REPAIR.search(f"{op_text or ''} {quote or ''}")


def oil_pressure_evidence(frags: dict) -> dict | None:
    """F02: a page of this engine's manual with the check by a control manometer AND the pressure norm."""
    for lab, f in (frags or {}).items():
        t = f.get("text") or ""
        if not f.get("extra") and _MANOMETER.search(t) and _PRESSURE_NORM.search(t) and re.search(r"давлен", t, re.I):
            return {"label": lab, "document": f.get("document"), "doc_id": f.get("doc_id"), "page": f.get("page")}
    return None


def repair_ops(ops: list[dict], code: str | None, frags: dict) -> tuple[list[dict], list[str]]:
    """Keep the works backed by an applicable page; [] if there is no repair work left (ticket refused)."""
    keep, errs = [], []
    for o in ops:
        f = frags.get(o.get("label") or "") or {}
        text = f.get("text") or ""
        if is_toc(text):
            errs.append(f"работа «{o['text']}»: стр. {o.get('page')} — оглавление, не инструкция")
        elif not concerns_unit(code, text + " " + o.get("text", "")):
            errs.append(f"работа «{o['text']}»: стр. {o.get('page')} не относится к узлу неисправности {code}")
        else:
            keep.append(o)
    if keep and code == "F02":
        ev = oil_pressure_evidence({o["label"]: frags[o["label"]] for o in keep if o.get("label") in frags}) or oil_pressure_evidence(frags)
        if not ev:
            errs.append("низкое давление масла: нет страницы с проверкой давления контрольным манометром и нормой давления")
            return [], errs
    if keep and all(is_inspection_only(o.get("text", ""), o.get("quote", "")) for o in keep):
        errs.append("в работах только осмотр/проверка — их выполняют на месте, заявка оформляется только на ремонт")
        return [], errs
    return keep, errs


def defect_recommendation(ch, vehicle) -> str:
    """Text the mechanic takes to the fleet's own defect-check procedure (outside the product)."""
    def ru(x):
        return "—" if x is None else f"{x:.2f}".replace(".", ",")
    vid = getattr(vehicle, "vehicle_id", "") or getattr(ch, "vehicle_id", "")
    eng = getattr(vehicle, "engine_model", "") or ""
    when = (getattr(ch, "checked_at", "") or "")[:16].replace("T", " ")
    return (f"Рекомендация на дефектовку. Машина {vid}" + (f", двигатель {eng}" if eng else "") + ". "
            f"Проверка {ch.session_id}" + (f" от {when}" if when else "") + f": {ch.anomaly_type} «{ch.anomaly_title or ''}», "
            f"оценка отклонения {ru(ch.anomaly_score)}, уверенность модели {ru(ch.confidence)}, качество записи {ch.signal_quality.value}. "
            f"В руководстве на двигатель {eng or 'этой машины'} нет инструкции по найденной неисправности, поэтому заявка в ТОиР не оформлена. "
            "Провести дефектовку узла по порядку автопарка.")
