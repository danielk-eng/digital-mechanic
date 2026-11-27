"""Deterministic policy and urgency (plain code, never an LLM decision).

Rules follow TZ v6 F3 and the customer's answers at the briefing (26.11):
* check_failed / bad record -> no conclusion about the engine, re-record; no override.
* normal -> keep operating, no ticket.
* anomaly -> explain, cite manual, ticket on mechanic's command.
* anomaly with low confidence or noisy record -> tell the mechanic, inspect the unit by
  the manual first (no re-recording needed); ticket after inspection confirms it.
* urgency: high = vehicle must not go on line (safety / expensive engine damage).

Organizers' answers in the task chat (26.11) and the day-2 briefing (27.11):
* confidence < 0,50 (RERECORD_CONFIDENCE) -> re-record / recheck, no ticket, no override.
* 0,50 <= confidence < 0,60 or signal_quality=warning -> the warning is always shown; inspection on site,
  ticket only after the mechanic consciously confirms it after the warning.
* the vehicle card wins over the model: a fault type impossible for the card's engine (F08 diesel fuel
  supply on a petrol engine) is a model error -> say so, no diagnosis by that type, no ticket.
* priorities: high = defect check before the trip, medium = at the nearest service, under observation,
  low = the unit is checked at the next inspection. A ban on going on line only when this engine's manual
  says so explicitly (see prohibition()).
"""

from __future__ import annotations

import re

from .schemas import CheckResult, CheckStatus, PolicyDecision, SignalQuality, Urgency

LOW_CONFIDENCE = 0.6  # below: inspection on site before any ticket
RERECORD_CONFIDENCE = 0.5  # below: re-record, no ticket (shown to the mechanic in the verdict card)

# Fault type -> fuel it can occur on (the rest fit any engine). Card fuel is ground truth.
FAULT_FUEL = {"F08": "дизель", "F12": "бензин", "F13": "бензин"}

# Customer: priority follows repair cost and direct impact on operation (engine damage is the most expensive).
FAULT_SEVERITY = {
    "F01": ("engine", "клапанный механизм: риск обрыва/загиба клапанов, дорогой ремонт двигателя"),
    "F02": ("engine_critical", "низкое давление масла: риск задира и проворота вкладышей, заклинивания"),
    "F03": ("engine_critical", "перегрев / ОЖ в масле: риск повреждения ГБЦ, прокладки, вкладышей"),
    "F04": ("aux", "ремённый привод: при обрыве — отказ генератора, насоса ОЖ, перегрев"),
    "F05": ("aux", "генератор: разряд АКБ, остановка в рейсе"),
    "F06": ("operation", "сцепление: непосредственно влияет на эксплуатацию"),
    "F07": ("operation", "коробка передач: непосредственно влияет на эксплуатацию"),
    "F08": ("engine", "топливоподача дизеля: жёсткая работа, риск повреждения ЦПГ"),
    "F09": ("engine", "турбокомпрессор: риск попадания обломков/масла в двигатель"),
    "F10": ("aux", "компрессор пневмотормозов / насос ГУР: влияет на тормоза и рулевое"),
    "F11": ("aux", "рециркуляция ОГ: потеря мощности, дымность"),
    "F12": ("aux", "зажигание: пропуски, перегрев катализатора"),
    "F13": ("aux", "подача бензина: перебои, остановка двигателя"),
    "F14": ("engine_critical", "цепь ГРМ / гидронатяжитель: при перескоке — встреча клапанов с поршнями"),
}


def doubt_flags(c: CheckResult) -> list[str]:
    out = []
    if c.signal_quality == SignalQuality.warning:
        out.append("запись с помехами — уверенность модели снижена")
    if c.confidence is not None and c.confidence < LOW_CONFIDENCE:
        out.append(f"низкая уверенность модели ({_ru(c.confidence)})")
    return out


def mismatch(c: CheckResult | None, vehicle=None) -> str | None:
    """Text for the mechanic when the fault type cannot occur on the card's engine, else None."""
    if not c or vehicle is None or c.status != CheckStatus.anomaly or not c.anomaly_type:
        return None
    need = FAULT_FUEL.get(c.anomaly_type)
    fuel = (getattr(vehicle, "fuel", "") or "").lower()
    if not need or not fuel or need in fuel:
        return None
    kind = {"дизель": "дизельного", "бензин": "бензинового"}[need]
    own = "бензиновый" if "бенз" in fuel else "дизельный" if "диз" in fuel else fuel
    return (f"Расхождение с карточкой машины: тип {c.anomaly_type} «{c.anomaly_title or ''}» бывает только у {kind} двигателя, "
            f"а в карточке {own} {getattr(vehicle, 'engine_model', '')}. Карточка верна — это ошибка модели диагностики, "
            "диагноз по этому типу не ставится, нормативы другого двигателя не подставляются. "
            "Повторите проверку или проведите дефектовку; заявка в ТОиР до уточнения данных не оформляется.")


def decide(c: CheckResult, vehicle=None) -> PolicyDecision:
    if c.status == CheckStatus.check_failed or c.signal_quality == SignalQuality.bad:
        return PolicyDecision.repeat_recording
    if c.status == CheckStatus.normal:
        return PolicyDecision.no_ticket_normal
    if mismatch(c, vehicle):
        return PolicyDecision.diagnosis_mismatch
    if c.confidence is not None and c.confidence < RERECORD_CONFIDENCE:
        return PolicyDecision.recheck_low_confidence
    if doubt_flags(c):
        return PolicyDecision.verify_then_ticket
    return PolicyDecision.propose_ticket


def may_create_ticket(c: CheckResult | None, vehicle=None) -> bool:
    return bool(c and c.status == CheckStatus.anomaly and c.signal_quality != SignalQuality.bad and c.anomaly_type
                and not (c.confidence is not None and c.confidence < RERECORD_CONFIDENCE) and not mismatch(c, vehicle))


def ticket_refusal(c: CheckResult | None, vehicle=None) -> str | None:
    """Why a ticket is not allowed (shown to the mechanic), None if allowed."""
    if not c:
        return "проверка не запрошена"
    if c.status == CheckStatus.normal:
        return "двигатель в норме — заявка не нужна"
    if c.status == CheckStatus.check_failed or c.signal_quality == SignalQuality.bad:
        return "запись плохого качества — выводов о двигателе нет, повторите запись"
    m = mismatch(c, vehicle)
    if m:
        return m
    if c.confidence is not None and c.confidence < RERECORD_CONFIDENCE:
        return (f"уверенность модели {_ru(c.confidence)} ниже порога {_ru(RERECORD_CONFIDENCE)} — повторите запись; "
                "заявка по этой проверке не оформляется")
    if not c.anomaly_type:
        return "нет отклонения"
    return None


_PROHIBIT = re.compile(r"(эксплуатац\w*[^.;]{0,60}(запрещ|не допуска)|(запрещ\w*|не допуска\w*)[^.;]{0,40}эксплуатац\w*|"
                       r"(запрещ\w*|не допуска\w*)[^.;]{0,30}(движени|работ\w* двигател|пуск\w* двигател))", re.I)


def prohibition(frags: dict) -> dict | None:
    """«Запрет выпуска в рейс» only if this engine's manual says operation is prohibited: first such page
    among the fragments found for the current fault (never the extra/vehicle manuals)."""
    for lab, f in (frags or {}).items():
        if f.get("extra"):
            continue
        m = _PROHIBIT.search(f.get("text") or "")
        if m:
            a = max(0, m.start() - 60)
            quote = (f["text"][a:m.end() + 60]).strip()
            return {"label": lab, "document": f.get("document"), "doc_id": f.get("doc_id"), "page": f.get("page"), "quote": quote}
    return None


def _ru(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


BASE_LEVEL = {"engine_critical": 2, "engine": 1, "operation": 1, "aux": 0}  # 2 high, 1 medium, 0 low
HIGH_SCORE = 0.8  # deviation score that raises the base level by one


def urgency(c: CheckResult) -> tuple[Urgency, list[str]]:
    """Base criticality of the fault type + a bounded adjustment (at most one level up or down) by the
    deviation score and the doubt about the record. The mechanic sees it and may change it on confirm."""
    why: list[str] = []
    score = c.anomaly_score or 0.0
    conf = c.confidence if c.confidence is not None else 1.0
    cls, text = FAULT_SEVERITY.get(c.anomaly_type or "", ("aux", "узел не классифицирован"))
    base = BASE_LEVEL[cls]
    why.append(f"базовая критичность типа — {['низкая', 'средняя', 'высокая'][base]}: {text}")
    adj = 0
    if score >= HIGH_SCORE:
        adj += 1; why.append(f"оценка отклонения {_ru(score)} (≥ {_ru(HIGH_SCORE)}): +1 уровень")
    else:
        why.append(f"оценка отклонения {_ru(score)} (< {_ru(HIGH_SCORE)}): без поправки")
    if conf < LOW_CONFIDENCE or c.signal_quality == SignalQuality.warning:
        adj -= 1; why.append("сомнение в записи (уверенность < 0,60 или помехи): −1 уровень, сначала осмотр узла")
    adj = max(-1, min(1, adj))
    lvl = max(0, min(2, base + adj))
    u = (Urgency.low, Urgency.medium, Urgency.high)[lvl]
    why.append("итог: " + URGENCY_MEANING_FULL[u.value] + "; окончательный приоритет выбирает механик при подтверждении")
    return u, why


URGENCY_MEANING_FULL = {"high": "высокий — дефектовка до выпуска в рейс", "medium": "средний — дефектовка на ближайшем ТО, машина работает под наблюдением",
                        "low": "низкий — машина работает, узел проверяется на следующей проверке"}


def fleet_status(c: CheckResult | None) -> str:
    """Fleet dashboard bucket for a vehicle by its latest check: ready | attention | quarantine."""
    if c is None:
        return "attention"
    if c.status == CheckStatus.normal:
        return "ready"
    if c.status == CheckStatus.check_failed:
        return "attention"
    u, _ = urgency(c)
    return "quarantine" if u == Urgency.high else "attention"


# Organizers' meanings (26.11). A recommendation, not a ban: the ban is only with the manual page (prohibition()).
LIGHT = {
    Urgency.high: ("stop", "Высокий: дефектовка до выпуска в рейс (рекомендация)"),
    Urgency.medium: ("service", "Средний: дефектовка на ближайшем ТО, машина работает под наблюдением"),
    Urgency.low: ("watch", "Низкий: машина работает, узел проверяется на следующей проверке"),
}
URGENCY_MEANING = {u.value: t.split(": ", 1)[1].replace(" (рекомендация)", "") for u, (_, t) in LIGHT.items()}
