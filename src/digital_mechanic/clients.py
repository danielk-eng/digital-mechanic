"""HTTP clients for Mock ML / Mock TOiR (URLs from .env) and the fleet file."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from . import adapters
from .schemas import CheckResult, TicketAck, TicketRequest, Vehicle


class ServiceError(Exception):
    def __init__(self, service: str, status: int | None, detail: str):
        super().__init__(f"{service}: {status} {detail}")
        self.service, self.status, self.detail = service, status, detail


class Fleet:
    """vehicles.json: list of vehicles and their check sessions (ML service has no listing)."""

    def __init__(self, path: str | Path, fault_types_path: str | Path | None = None):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self._raw = {v["vehicle_id"]: v for v in raw["vehicles"]}
        self.fault_types: dict[str, str] = {}
        if fault_types_path and Path(fault_types_path).exists():
            self.fault_types = {f["code"]: f["title"] for f in json.loads(Path(fault_types_path).read_text(encoding="utf-8"))}

    def ids(self) -> list[str]:
        return list(self._raw)

    def get(self, vehicle_id: str) -> Vehicle:
        if vehicle_id not in self._raw:  # plate is Latin, case-sensitive
            raise KeyError(vehicle_id)
        return adapters.vehicle_from_official(self._raw[vehicle_id])

    def sessions(self, vehicle_id: str) -> list[CheckResult]:
        return [adapters.check_from_official(s) for s in self._raw[vehicle_id]["sessions"]]

    def latest_session_id(self, vehicle_id: str) -> str:
        return self._raw[vehicle_id]["sessions"][-1]["session_id"]

    def all(self) -> list[Vehicle]:
        return [adapters.vehicle_from_official(v) for v in self._raw.values()]


class MlClient:
    def __init__(self, base_url: str, timeout: float = 20.0, http=None):
        # http: anything with httpx-like .get/.post (e.g. an in-process TestClient for embedded mocks)
        self.base, self.timeout, self.http = base_url.rstrip("/"), timeout, http or httpx

    def diagnose(self, vehicle_id: str, session_id: str) -> CheckResult:
        try:
            r = self.http.post(f"{self.base}/diagnose", json={"vehicle_id": vehicle_id, "session_id": session_id}, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ServiceError("ml", None, type(e).__name__) from None
        if r.status_code != 200:
            raise ServiceError("ml", r.status_code, r.text[:300])
        return adapters.check_from_official(r.json())

    def health(self) -> bool:
        try:
            return self.http.get(f"{self.base}/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False


class ToirClient:
    def __init__(self, base_url: str, timeout: float = 20.0, http=None):
        # http: anything with httpx-like .get/.post (e.g. an in-process TestClient for embedded mocks)
        self.base, self.timeout, self.http = base_url.rstrip("/"), timeout, http or httpx

    def create_ticket(self, t: TicketRequest) -> TicketAck:
        try:
            r = self.http.post(f"{self.base}/tickets", json=adapters.ticket_to_official(t), timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ServiceError("toir", None, type(e).__name__) from None
        if r.status_code != 201:
            raise ServiceError("toir", r.status_code, r.text[:300])
        d = r.json()
        return TicketAck(ticket_id=d["ticket_id"], status=d.get("status", "created"))

    def health(self) -> bool:
        try:
            return self.http.get(f"{self.base}/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False
