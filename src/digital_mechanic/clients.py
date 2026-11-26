"""HTTP clients for the ML and TOIR services plus the local fleet file."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from . import adapters
from .schemas import CheckResult, TicketAck, TicketDraft, Vehicle


class Fleet:
    def __init__(self, path: str | Path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        items = raw["vehicles"] if isinstance(raw, dict) else raw
        self._raw = {str(v.get("vehicle_id")): v for v in items}

    def ids(self) -> list[str]:
        return list(self._raw)

    def get(self, vehicle_id: str) -> Vehicle:
        if vehicle_id not in self._raw:  # case matters
            raise KeyError(f"unknown vehicle_id {vehicle_id!r}")
        return adapters.vehicle_from_official(self._raw[vehicle_id])

    def listing(self) -> list[dict]:
        out = []
        for vid, v in self._raw.items():
            ve = adapters.vehicle_from_official(v)
            out.append({"vehicle_id": vid, "label": f"{vid} · {ve.model or ''} · {ve.engine_type}"})
        return out


class MlClient:
    def __init__(self, base_url: str, timeout: float = 20.0):
        self.base, self.timeout = base_url.rstrip("/"), timeout

    def list_checks(self, vehicle_id: str) -> list[dict]:
        r = httpx.get(f"{self.base}/api/v1/vehicles/{vehicle_id}/checks", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_check(self, vehicle_id: str, session_id: str) -> CheckResult:
        r = httpx.get(f"{self.base}/api/v1/vehicles/{vehicle_id}/checks/{session_id}", timeout=self.timeout)
        r.raise_for_status()
        return adapters.check_from_official(r.json(), vehicle_id)


class ToirClient:
    def __init__(self, base_url: str, timeout: float = 20.0):
        self.base, self.timeout = base_url.rstrip("/"), timeout

    def create_ticket(self, draft: TicketDraft) -> TicketAck:
        r = httpx.post(f"{self.base}/api/v1/tickets", json=adapters.ticket_to_official(draft), timeout=self.timeout)
        r.raise_for_status()
        return TicketAck(ticket_id=r.json()["ticket_id"])
