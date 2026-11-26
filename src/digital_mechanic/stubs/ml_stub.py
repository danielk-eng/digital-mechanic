"""MOCK ML service. Replaced by the official FastAPI stub after the briefing archive is available.

Request by ids only (no audio). The answer has no engine model, as in the official description.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

app = FastAPI(title="MOCK ML stub", version="mock")
_PATH = Path(os.environ.get("VEHICLES_PATH", "data/mock/vehicles.json"))


def _fleet() -> dict:
    return {v["vehicle_id"]: v for v in json.loads(_PATH.read_text(encoding="utf-8"))["vehicles"]}


@app.get("/api/v1/vehicles/{vehicle_id}/checks")
def list_checks(vehicle_id: str):
    v = _fleet().get(vehicle_id)
    if not v:
        raise HTTPException(404, "unknown vehicle")
    return [{"session_id": c["session_id"], "datetime": c["datetime"]} for c in v["checks"]]


@app.get("/api/v1/vehicles/{vehicle_id}/checks/{session_id}")
def get_check(vehicle_id: str, session_id: str):
    v = _fleet().get(vehicle_id)
    c = next((c for c in (v or {}).get("checks", []) if c["session_id"] == session_id), None)
    if not c:
        raise HTTPException(404, "unknown check")
    return c
