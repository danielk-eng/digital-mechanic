"""Tenants (companies). A request runs with a scope = the set of trucks of the caller's company (None for the engineer:
all companies). The fleet seen by the endpoints and by the agent's tools is filtered by that scope, so a chief or a
mechanic of one company never reads, diagnoses or chats about another company's truck."""
from __future__ import annotations

import contextvars

SEED_ID = "neurodvs"
SEED_NAME = "НейроДВС"
SCOPE: contextvars.ContextVar[frozenset | None] = contextvars.ContextVar("vehicle_scope", default=None)


def company_of(u: dict | None) -> str | None:
    """None = all companies (engineer). Accounts created before tenants belong to the seed company."""
    if not u or u.get("role") == "engineer":
        return None
    return u.get("company_id") or SEED_ID


def ensure_seed(store) -> None:
    import time
    if not store.one("SELECT id FROM companies WHERE id=?", (SEED_ID,)):
        store.x("INSERT INTO companies(id, name, created) VALUES (?,?,?)", (SEED_ID, SEED_NAME, time.time()))


def vehicle_company(store, all_ids: list[str]) -> dict[str, str]:
    m = {r["vehicle_id"]: r["company_id"] for r in store.q("SELECT vehicle_id, company_id FROM company_vehicles")}
    return {v: m.get(v) or SEED_ID for v in all_ids}


def scope_for(store, fleet_ids: list[str], company_id: str | None) -> frozenset | None:
    if company_id is None:
        return None
    return frozenset(v for v, c in vehicle_company(store, fleet_ids).items() if c == company_id)


class ScopedFleet:
    """The fleet as one company sees it. Everything else passes through to the real fleet."""

    def __init__(self, fleet):
        self._f = fleet

    def __getattr__(self, name):
        return getattr(self._f, name)

    def _ok(self, vid: str) -> bool:
        sc = SCOPE.get()
        return sc is None or vid in sc

    def ids(self) -> list[str]:
        return [v for v in self._f.ids() if self._ok(v)]

    def all(self):
        return [v for v in self._f.all() if self._ok(v.vehicle_id)]

    def get(self, vehicle_id: str):
        if not self._ok(vehicle_id):
            raise KeyError(vehicle_id)
        return self._f.get(vehicle_id)

    def all_ids(self) -> list[str]:
        return self._f.ids()
