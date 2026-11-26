"""Web UI + JSON API (mobile-first chat). Run: uvicorn digital_mechanic.demo.app:app"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..bootstrap import build_agent

app = FastAPI(title="Digital Mechanic")
STATIC = Path(__file__).parent / "static"
_agent = None


def agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


class ChatIn(BaseModel):
    sid: str | None = None
    text: str


class SelectIn(BaseModel):
    sid: str | None = None
    vehicle_id: str


class CheckIn(BaseModel):
    sid: str
    session_id: str | None = None


class ConfirmIn(BaseModel):
    sid: str
    draft_id: str | None = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/vehicles")
def vehicles():
    return agent().fleet.listing()


@app.post("/api/select")
def select(b: SelectIn):
    try:
        return agent().select_vehicle(b.sid or agent().session(None).sid, b.vehicle_id)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.post("/api/check")
def check(b: CheckIn):
    return agent().run_check(b.sid, b.session_id)


@app.post("/api/chat")
def chat(b: ChatIn):
    return agent().chat(b.sid or agent().session(None).sid, b.text)


@app.post("/api/confirm")
def confirm(b: ConfirmIn):
    return agent().confirm(b.sid, b.draft_id)


@app.post("/api/cancel")
def cancel(b: ConfirmIn):
    return agent().cancel(b.sid)


@app.get("/api/trace/{sid}")
def trace(sid: str):
    return {"session": sid, "events": agent().session(sid).trace}


@app.get("/api/usage")
def usage():
    return agent().llm.usage.__dict__


@app.get("/api/docs")
def docs():
    """Map official document title -> URL of the PDF (for page links)."""
    return {p.document: f"/manual/{p.doc_id}" for p in agent().index.pages}


@app.get("/manual/{doc_id}")
def manual(doc_id: str):
    p = next((p for p in agent().index.pages if p.doc_id == doc_id), None)
    if not p:
        return JSONResponse({"error": "unknown document"}, status_code=404)
    return FileResponse(p.path, media_type="application/pdf")


@app.get("/health")
def health():
    return {"ok": True, "llm": agent().llm.enabled}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
