"""Deterministic priority with a human-readable rationale (shown to the mechanic).

Thresholds are the team's own choice: the customer gave no priority tables.
"""

from __future__ import annotations

from .schemas import CheckResult, Priority, RecordingQuality

# Units whose failure quickly becomes an engine-out failure.
CRITICAL_MARKERS = ("кривошипно", "шатун", "коленчат", "вкладыш", "поршн", "смазк", "давлени масл", "детонац", "заклин")


def compute_priority(check: CheckResult) -> tuple[Priority, list[str]]:
    why: list[str] = []
    score = check.anomaly_score or 0.0
    conf = check.confidence or 0.0
    title = (check.fault_title or "").lower()
    points = 0
    if score >= 0.75:
        points += 2; why.append(f"высокая оценка отклонения {score:.2f} (порог 0,75)")
    elif score >= 0.5:
        points += 1; why.append(f"средняя оценка отклонения {score:.2f} (порог 0,50)")
    else:
        why.append(f"оценка отклонения невысокая: {score:.2f}")
    if conf >= 0.8:
        points += 1; why.append(f"уверенность модели высокая: {conf:.2f}")
    elif conf < 0.6:
        points -= 1; why.append(f"уверенность модели низкая: {conf:.2f}, приоритет не завышаем")
    if any(m in title for m in CRITICAL_MARKERS):
        points += 1; why.append("узел критичный: возможен аварийный отказ двигателя")
    if check.quality == RecordingQuality.warning:
        points -= 1; why.append("качество записи «помехи»: вывод менее надёжен")
    prio = Priority.high if points >= 3 else Priority.medium if points >= 1 else Priority.low
    return prio, why


def doubt_flags(check: CheckResult) -> list[str]:
    """Signals that must be reported to the mechanic and never lead to a silent ticket."""
    out = []
    if check.quality == RecordingQuality.warning:
        out.append("запись с помехами")
    if check.confidence is not None and check.confidence < 0.6:
        out.append(f"низкая уверенность модели ({check.confidence:.2f})")
    if check.anomaly_score is not None and check.confidence is not None:
        if (check.anomaly_score >= 0.7 and check.confidence < 0.6) or (check.anomaly_score < 0.4 and check.confidence >= 0.8):
            out.append("оценка отклонения и уверенность расходятся")
    return out
