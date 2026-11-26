"""Single place mapping external payloads onto canonical models (official names since TZ v6)."""

from __future__ import annotations

from typing import Any

from .schemas import CheckResult, TicketRequest, Vehicle


def vehicle_from_official(payload: dict[str, Any]) -> Vehicle:
    return Vehicle(**{k: payload.get(k) for k in Vehicle.model_fields})


def check_from_official(payload: dict[str, Any]) -> CheckResult:
    return CheckResult(**{k: payload.get(k) for k in CheckResult.model_fields if k in payload})


def ticket_to_official(t: TicketRequest) -> dict[str, Any]:
    return t.model_dump(mode="json")
