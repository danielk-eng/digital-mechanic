"""Deterministic policy and urgency (plain code, never an LLM decision).

Rules follow TZ v6 F3 and the customer's answers at the briefing (26.11):
* check_failed / bad record -> no conclusion about the engine, re-record; no override.
* normal -> keep operating, no ticket.
* anomaly -> explain, cite manual, ticket on mechanic's command.
* anomaly with low confidence or noisy record -> tell the mechanic, inspect the unit by
  the manual first (no re-recording needed); ticket after inspection confirms it.
* urgency: high = vehicle must not go on line (safety / expensive engine damage).
"""

from __future__ import annotations

from .schemas import CheckResult, CheckStatus, PolicyDecision, SignalQuality, Urgency

LOW_CONFIDENCE = 0.6

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
        out.append(f"низкая уверенность модели ({c.confidence:.2f})")
    return out


def decide(c: CheckResult) -> PolicyDecision:
    if c.status == CheckStatus.check_failed or c.signal_quality == SignalQuality.bad:
        return PolicyDecision.repeat_recording
    if c.status == CheckStatus.normal:
        return PolicyDecision.no_ticket_normal
    if doubt_flags(c):
        return PolicyDecision.verify_then_ticket
    return PolicyDecision.propose_ticket


def may_create_ticket(c: CheckResult | None) -> bool:
    return bool(c and c.status == CheckStatus.anomaly and c.signal_quality != SignalQuality.bad and c.anomaly_type)


def urgency(c: CheckResult) -> tuple[Urgency, list[str]]:
    why: list[str] = []
    score = c.anomaly_score or 0.0
    conf = c.confidence or 0.0
    cls, text = FAULT_SEVERITY.get(c.anomaly_type or "", ("aux", "узел не классифицирован"))
    why.append(text)
    pts = {"engine_critical": 2, "engine": 1, "operation": 1, "aux": 0}[cls]
    if score >= 0.8:
        pts += 2; why.append(f"высокая оценка отклонения {score:.2f} (≥ 0,80)")
    elif score >= 0.65:
        pts += 1; why.append(f"заметная оценка отклонения {score:.2f} (≥ 0,65)")
    else:
        why.append(f"умеренная оценка отклонения {score:.2f}")
    if conf < LOW_CONFIDENCE:
        pts -= 1; why.append(f"уверенность модели низкая ({conf:.2f}): сначала проверить узел")
    if c.signal_quality == SignalQuality.warning:
        pts -= 1; why.append("запись с помехами")
    u = Urgency.high if pts >= 3 else Urgency.medium if pts >= 1 else Urgency.low
    return u, why


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


LIGHT = {
    Urgency.high: ("stop", "СТОП: на линию не выпускать до дефектовки"),
    Urgency.medium: ("service", "Требует внимания: дефектовка в ближайшее время"),
    Urgency.low: ("watch", "Можно эксплуатировать, контроль на следующем ТО"),
}
