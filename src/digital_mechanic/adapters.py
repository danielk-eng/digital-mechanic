"""Single place to map official OpenAPI / vehicles.json field names onto canonical schemas.

Official names are unknown until the briefing archive is available, so the mappers accept
several aliases. Once the official spec is known, trim the alias lists here (nowhere else).
"""

from __future__ import annotations

from typing import Any, Optional

from .schemas import CheckResult, CheckStatus, RecordingQuality, TicketDraft, Vehicle

_STATUS = {
    "normal": CheckStatus.normal, "ok": CheckStatus.normal, "норма": CheckStatus.normal,
    "anomaly": CheckStatus.anomaly, "deviation": CheckStatus.anomaly, "отклонение": CheckStatus.anomaly,
    "check_failed": CheckStatus.check_failed, "failed": CheckStatus.check_failed, "failure": CheckStatus.check_failed,
    "recording_failed": CheckStatus.check_failed, "сбой записи": CheckStatus.check_failed, "сбой": CheckStatus.check_failed,
}
_QUALITY = {
    "good": RecordingQuality.good, "ok": RecordingQuality.good, "хорошее": RecordingQuality.good,
    "warning": RecordingQuality.warning, "noisy": RecordingQuality.warning, "помехи": RecordingQuality.warning,
    "bad": RecordingQuality.bad, "poor": RecordingQuality.bad, "плохое": RecordingQuality.bad,
}


def _first(d: dict[str, Any], *keys: str) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def vehicle_from_official(payload: dict[str, Any]) -> Vehicle:
    make = _first(payload, "make", "brand")
    model = _first(payload, "model")
    return Vehicle(
        vehicle_id=str(_first(payload, "vehicle_id", "id")),
        engine_type=str(_first(payload, "engine_model", "engine_type", "engine")),
        model=" ".join(x for x in (make, model) if x) or None,
    )


def check_from_official(payload: dict[str, Any], vehicle_id: str | None = None) -> CheckResult:
    status = str(_first(payload, "status", "check_status") or "").lower()
    quality = _first(payload, "quality", "recording_quality")
    return CheckResult(
        vehicle_id=str(_first(payload, "vehicle_id") or vehicle_id),
        session_id=str(_first(payload, "session_id", "check_id", "id")),
        status=_STATUS[status],
        fault_code=_first(payload, "fault_id", "fault_code", "fault_type_code"),
        fault_title=_first(payload, "fault_name", "fault_title", "fault_type_name"),
        anomaly_score=_first(payload, "anomaly_score", "deviation_score"),
        confidence=_first(payload, "confidence"),
        quality=_QUALITY[str(quality).lower()] if quality is not None else None,
        symptoms=_first(payload, "description", "symptoms", "features"),
        model_version=_first(payload, "model_version"),
    )


def ticket_to_official(draft: TicketDraft) -> dict[str, Any]:
    return {
        "vehicle_id": draft.vehicle_id,
        "session_id": draft.session_id,
        "fault_code": draft.fault_code or "",
        "diagnosis": draft.diagnosis,
        "priority": draft.priority.value,
        "works": [w.title for w in draft.works],
        "parts": [p.title for p in draft.parts],
        "references": [{"document": s.document, "page": s.page} for s in draft.source_pages],
    }
