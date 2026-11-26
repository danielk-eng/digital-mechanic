"""MOCK TOIR service: validates format, returns TKT-0001, TKT-0002 ... Does not list tickets."""

from __future__ import annotations

import itertools

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="MOCK TOIR stub", version="mock")
_counter = itertools.count(1)


class Ref(BaseModel):
    document: str = Field(..., min_length=1)
    page: int = Field(..., ge=1)


class Ticket(BaseModel):
    vehicle_id: str
    session_id: str
    fault_code: str
    diagnosis: str
    priority: str
    works: list[str] = Field(..., min_length=1)
    parts: list[str] = []
    references: list[Ref] = Field(..., min_length=1)


@app.post("/api/v1/tickets")
def create(t: Ticket):
    if t.priority not in {"low", "medium", "high"}:
        raise HTTPException(422, "bad priority")
    return {"ticket_id": f"TKT-{next(_counter):04d}"}
