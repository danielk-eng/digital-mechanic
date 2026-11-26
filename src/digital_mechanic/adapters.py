"""Single place to map official OpenAPI field names onto canonical schemas.

Fill this in during hours 0–2 after the brief, do not scatter aliases elsewhere.
"""

from __future__ import annotations

from typing import Any

from .schemas import CheckResult, TicketDraft, Vehicle


def vehicle_from_official(payload: dict[str, Any]) -> Vehicle:
    raise NotImplementedError("map official vehicles.json after the 26.11 brief")


def check_from_official(payload: dict[str, Any]) -> CheckResult:
    raise NotImplementedError("map official ML OpenAPI after the 26.11 brief")


def ticket_to_official(draft: TicketDraft) -> dict[str, Any]:
    raise NotImplementedError("map official TOIR OpenAPI after the 26.11 brief")
