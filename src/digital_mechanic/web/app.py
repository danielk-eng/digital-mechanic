"""FastAPI app: mechanic chat (mobile), chief mechanic dashboard, voice, photos, audit, 152-FZ."""

from __future__ import annotations

import base64
import csv
import io
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import pii, voice
from ..agent.llm import LlmUnavailable
from ..agent.prompts import VISION_PROMPT
from ..bootstrap import build_agent
from ..clients import ServiceError
from ..config import get_settings
from ..history import mask_name
from ..i18n import LANGS, lang_or_default

S = get_settings()
AGENT = build_agent(S)
STORE = AGENT.store
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Цифровой механик", version="1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
Path(S.log_path).parent.mkdir(parents=True, exist_ok=True)


def jlog(**kw) -> None:
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(S.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def user_of(uid: str | None, manager: bool = False) -> dict:
    u = STORE.user(uid) if uid else None
    if not u:
        raise HTTPException(401, "login required")
    if manager and u["role"] != "manager":
        raise HTTPException(403, "manager only")
    return u


_LAST_PURGE = [0.0]


@app.on_event("startup")
def _purge() -> None:
    """Retention (152-FZ): at startup and then at most hourly, triggered by logins."""
    _LAST_PURGE[0] = time.time()
    STORE.purge(S.audit_retention_days)


# ---------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/manager", response_class=HTMLResponse)
def manager_page() -> str:
    return (STATIC / "manager.html").read_text(encoding="utf-8")


@app.get("/privacy", response_class=HTMLResponse)
def privacy() -> str:
    return (STATIC / "privacy.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- auth / profile
class LoginIn(BaseModel):
    name: str
    role: str = "mechanic"
    lang: str = "ru"
    experience: str = "experienced"
    consent: bool = False


@app.post("/api/login")
def login(body: LoginIn, response: Response) -> dict:
    if S.require_consent and not body.consent:
        raise HTTPException(400, "consent required (152-FZ)")
    if time.time() - _LAST_PURGE[0] > 3600:
        _purge()
    uid = uuid.uuid4().hex
    role = "manager" if body.role == "manager" else "mechanic"
    u = STORE.upsert_user(uid, name=pii.mask(body.name.strip()[:60]) or "—", role=role, lang=lang_or_default(body.lang),
                          experience=body.experience if body.experience in EXPERIENCE_LEVELS else "experienced", consent_at=time.time())
    response.set_cookie("uid", uid, httponly=True, samesite="lax", max_age=30 * 86400)
    STORE.audit(user_id=uid, role=role, action="login", ok=1)
    return {"user": u}


@app.get("/api/me")
def me(uid: str | None = Cookie(None)) -> dict:
    return {"user": user_of(uid), "langs": {k: v["name"] for k, v in LANGS.items()}}


EXPERIENCE_LEVELS = ("novice", "experienced", "expert")
DETAIL_LEVELS = ("brief", "detailed")


class ProfileIn(BaseModel):
    lang: str | None = None
    experience: str | None = None
    detail: str | None = None
    big_font: bool | None = None
    voice_out: bool | None = None


@app.post("/api/me")
def update_me(body: ProfileIn, uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    kw = {k: v for k, v in body.model_dump().items() if v is not None}
    if "lang" in kw:
        kw["lang"] = lang_or_default(kw["lang"])
    if kw.get("experience") not in (None, *EXPERIENCE_LEVELS):
        raise HTTPException(422, "experience: novice | experienced | expert")
    if kw.get("detail") not in (None, *DETAIL_LEVELS):
        raise HTTPException(422, "detail: brief | detailed")
    for k in ("big_font", "voice_out"):
        if k in kw:
            kw[k] = int(kw[k])
    return {"user": STORE.upsert_user(uid, **kw)}


@app.delete("/api/me")
def delete_me(response: Response, uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    STORE.delete_user_data(uid)
    response.delete_cookie("uid")
    return {"deleted": True}


@app.post("/api/logout")
def logout(response: Response) -> dict:
    response.delete_cookie("uid")
    return {"ok": True}


# ---------------------------------------------------------------- fleet / vehicles
@app.get("/api/vehicles")
def vehicles(uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    ov = AGENT.fleet_overview()
    return {"vehicles": [{k: r[k] for k in ("vehicle_id", "garage_no", "model", "engine_model", "status", "in_repair")} for r in sorted(ov["vehicles"], key=lambda r: r["vehicle_id"])]}


@app.get("/api/vehicle/{vid}")
def vehicle(vid: str, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    try:
        v = AGENT.fleet.get(vid)
    except KeyError:
        raise HTTPException(404, "unknown vehicle")
    c = AGENT.conv(u["id"], vid)
    msgs = [{"role": m["role"], "text": m["content"], "meta": json.loads(m["meta"] or "{}")} for m in STORE.messages(c.cid, 40)]
    return {"vehicle": v.model_dump(), "sessions": [s.model_dump(mode="json") for s in AGENT.fleet.sessions(vid)],
            "tickets": STORE.tickets(vid), "repair": AGENT.repair_card(vid), "manuals": AGENT.manuals_for(v.engine_model),
            "draft": AGENT.draft_card(c) if c.draft else None, "messages": msgs,
            "check": c.check.model_dump(mode="json") if c.check else None}


@app.get("/api/fleet")
def fleet(uid: str | None = Cookie(None)) -> dict:
    user_of(uid, manager=True)
    return AGENT.fleet_overview()


@app.get("/api/fleet/{vid}")
def fleet_vehicle(vid: str, uid: str | None = Cookie(None)) -> dict:
    user_of(uid, manager=True)
    if vid not in AGENT.fleet.ids():
        raise HTTPException(404)
    h = AGENT.tool_history(vid)
    h["vehicle"] = AGENT.fleet.get(vid).model_dump()
    h["repair"] = AGENT.repair_card(vid)
    h["diagnoses_run"] = STORE.q("SELECT ts,session_id,status,decision,urgency FROM diagnoses WHERE vehicle_id=? ORDER BY ts DESC LIMIT 20", (vid,))
    return h


# ---------------------------------------------------------------- agent
def _record(u: dict, c, action: str, inp: str, turn, t0: float, card_meta: dict | None = None) -> None:
    lat = int((time.time() - t0) * 1000)
    if inp:
        STORE.add_message(c.cid, u["id"], c.vehicle.vehicle_id if c.vehicle else None, "user", pii.mask(inp))
    STORE.add_message(c.cid, u["id"], c.vehicle.vehicle_id if c.vehicle else None, "assistant", turn.text,
                      {"cards": turn.cards, "sources": turn.sources, **(card_meta or {})})
    STORE.audit(user_id=u["id"], role=u["role"], vehicle_id=c.vehicle.vehicle_id if c.vehicle else None, action=action, input=pii.mask(inp)[:2000],
                output=turn.text[:4000], tools=turn.tools, sources=[f"{s['document']} стр.{s['page']}" for s in turn.sources], latency_ms=lat, tokens=turn.tokens, ok=int(turn.ok))
    jlog(event=action, user=u["id"][:8], vehicle=c.vehicle.vehicle_id if c.vehicle else None, latency_ms=lat, tokens=turn.tokens, tools=[t["tool"] for t in turn.tools], ok=turn.ok)


class VehIn(BaseModel):
    vehicle_id: str
    note: str = ""


def _conv_for(u: dict, vid: str):
    if vid not in AGENT.fleet.ids():
        raise HTTPException(404, "unknown vehicle")
    return AGENT.conv(u["id"], vid)


@app.post("/api/summary")
def session_summary(body: VehIn, uid: str | None = Cookie(None)) -> dict:
    """Item 9: short summary of this vehicle's session for the next shift / the chief."""
    u = user_of(uid)
    s = AGENT.summary(_conv_for(u, body.vehicle_id), u)
    STORE.audit(user_id=u["id"], role=u["role"], vehicle_id=body.vehicle_id, action="summary", output=s["text"][:2000], ok=1)
    return {"text": s["text"], "llm": s["llm"], "pages": s["package"]["pages"]}


@app.post("/api/handoff")
def handoff(body: VehIn, uid: str | None = Cookie(None)) -> dict:
    """Item 8: hand the case to the chief mechanic with a package (vehicle, ML verdict, dialog, pages, photos)."""
    u = user_of(uid)
    r = AGENT.handoff(_conv_for(u, body.vehicle_id), u, body.note)
    STORE.audit(user_id=u["id"], role=u["role"], vehicle_id=body.vehicle_id, action="handoff", input=pii.mask(body.note)[:500], output=r["summary"][:2000], ok=1)
    return r


class InspIn(BaseModel):
    vehicle_id: str
    items: list[dict] = []
    note: str = ""


@app.post("/api/inspection/draft")
def inspection_draft(body: InspIn, uid: str | None = Cookie(None)) -> dict:
    """Item 15: inspection draft from photos and voice/text notes of this visit; the mechanic must review it."""
    u = user_of(uid)
    from ..inspection import draft

    return draft(AGENT, _conv_for(u, body.vehicle_id))


@app.post("/api/inspection/confirm")
def inspection_confirm(body: InspIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    items = [{k: (pii.mask(str(v))[:400] if isinstance(v, str) else v) for k, v in it.items() if k in ("component", "finding", "severity", "source", "doc_id", "page", "document", "confirmed")}
             for it in body.items[:20] if it.get("confirmed")]
    if not items:
        raise HTTPException(400, "nothing confirmed")
    hid = STORE.add_handoff(u["id"], body.vehicle_id, "inspection", pii.mask(body.note)[:500], "", {"items": items})
    STORE.audit(user_id=u["id"], role=u["role"], vehicle_id=body.vehicle_id, action="inspection", output=json.dumps(items, ensure_ascii=False)[:2000], ok=1)
    return {"id": hid, "items": items}


@app.get("/api/proven")
def proven(vehicle_id: str, code: str | None = None, uid: str | None = Cookie(None)) -> dict:
    """Item 6: proven fixes for this engine + fault code from this fleet's tickets/repairs."""
    user_of(uid)
    if vehicle_id not in AGENT.fleet.ids():
        raise HTTPException(404)
    from ..proven import proven_fixes

    if not code:
        c = AGENT.conv(user_of(uid)["id"], vehicle_id)
        code = c.check.anomaly_type if c.check else None
    v = AGENT.fleet.get(vehicle_id)
    return {"engine": v.engine_model, "code": code, "fixes": proven_fixes(AGENT, vehicle_id, code)}


@app.get("/api/tickets")
def tickets_list(uid: str | None = Cookie(None)) -> dict:
    user_of(uid, manager=True)
    out = []
    for t in STORE.tickets():
        p = t["payload"]
        out.append({"ticket_id": t["ticket_id"], "vehicle_id": t["vehicle_id"], "urgency": t["urgency"], "anomaly_type": t["anomaly_type"],
                    "at": time.strftime("%d.%m.%Y %H:%M", time.localtime(t["ts"])), "conclusion": p.get("conclusion", ""),
                    "operations": p.get("operations", []), "status": t.get("status"), "attachments": t.get("attachments"), "review": t.get("review")})
    return {"tickets": out}


class ReviewIn(BaseModel):
    decision: str
    comment: str = ""


@app.post("/api/ticket/{ticket_id}/review")
def review_ticket(ticket_id: str, body: ReviewIn, uid: str | None = Cookie(None)) -> dict:
    """Item 12: the chief mechanic approves the ticket or returns it to the mechanic with a comment."""
    u = user_of(uid, manager=True)
    if body.decision not in ("approved", "returned"):
        raise HTTPException(400, "decision")
    if not STORE.one("SELECT id FROM tickets WHERE ticket_id=?", (ticket_id,)):
        raise HTTPException(404)
    rev = {"decision": body.decision, "comment": pii.mask(body.comment)[:500], "by": mask_name(u.get("name") or ""), "at": time.time()}
    STORE.set_ticket_review(ticket_id, rev, body.decision)
    STORE.audit(user_id=u["id"], role=u["role"], action="ticket_review", input=f"{ticket_id} {body.decision}", output=rev["comment"], ok=1)
    return {"ok": True, "review": rev}


@app.get("/api/handoffs")
def handoffs(status: str | None = None, uid: str | None = Cookie(None)) -> dict:
    user_of(uid, manager=True)
    rows = STORE.handoffs(status)
    for r in rows:
        r["user_name"] = mask_name(r.get("user_name") or "")
    return {"handoffs": rows}


class ResolveIn(BaseModel):
    status: str
    comment: str = ""


@app.post("/api/handoff/{hid}")
def resolve_handoff(hid: int, body: ResolveIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid, manager=True)
    if body.status not in ("taken", "closed", "open"):
        raise HTTPException(400, "status")
    STORE.resolve_handoff(hid, u["id"], body.status, pii.mask(body.comment)[:500])
    return {"ok": True}


@app.get("/api/photo/{name}")
def photo_file(name: str, uid: str | None = Cookie(None)):
    user_of(uid, manager=True)
    if not re.fullmatch(r"[\w.-]{3,80}", name) or not (Path(S.uploads_dir) / name).is_file():
        raise HTTPException(404)
    return FileResponse(Path(S.uploads_dir) / name)


class DiagIn(BaseModel):
    vehicle_id: str
    session_id: str | None = None


@app.post("/api/diagnose")
def diagnose(body: DiagIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    t0 = time.time()
    try:
        c = AGENT.conv(u["id"], body.vehicle_id)
    except KeyError:
        raise HTTPException(404, "unknown vehicle")
    try:
        card = AGENT.diagnose(c, body.session_id)
    except ServiceError as e:
        STORE.audit(user_id=u["id"], role=u["role"], vehicle_id=body.vehicle_id, action="diagnose", output=str(e), ok=0)
        raise HTTPException(502, f"ML-сервис: {e.status} {e.detail[:200]}")
    from ..agent.core import Turn

    t = Turn(text="", cards=[card], tools=[{"tool": "ml.diagnose", "session_id": card["check"]["session_id"], "status": card["status"], "ok": True}])
    _record(u, c, "diagnose", "", t, t0)
    return {"card": card}


class ChatIn(BaseModel):
    vehicle_id: str | None = None
    text: str = ""
    event: str | None = None


@app.post("/api/chat")
def chat(body: ChatIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    t0 = time.time()
    try:
        c = AGENT.conv(u["id"], body.vehicle_id)
    except KeyError:
        raise HTTPException(404, "unknown vehicle")
    if c.vehicle is None and u["role"] != "manager":
        raise HTTPException(400, "choose a vehicle")
    turn = AGENT.handle(c, u, body.text[:4000], event=body.event)
    # "expand" re-renders the previous answer: do not store it again as a user message
    shown = {"expand": "", "alternatives": "🔀 Другие варианты"}.get(body.event or "", None)
    _record(u, c, "chat" if not body.event else body.event, body.text if shown is None else shown, turn, t0)
    return {**turn.as_dict(), "latency_ms": int((time.time() - t0) * 1000)}


@app.get("/api/progress")
def progress(vehicle_id: str | None = None, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    return {"steps": AGENT.progress.get(f"{u['id']}:{vehicle_id or '-'}", [])}


@app.get("/api/history/{vid}")
def vehicle_history(vid: str, uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    try:
        AGENT.fleet.get(vid)
    except KeyError:
        raise HTTPException(404, "unknown vehicle")
    return AGENT.timeline(vid)


@app.get("/api/suggest")
def suggest_chips(vehicle_id: str | None = None, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    c = AGENT.conv(u["id"], vehicle_id)
    return {"chips": AGENT.suggestions(c, u)}


PLATE_MAP = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")


def find_plate(text: str) -> str | None:
    m = re.search(r"ГОСНОМЕР:\s*([^\n]+)", text, re.I)
    cand = (m.group(1) if m else text).upper().translate(PLATE_MAP)
    cand = re.sub(r"[^A-Z0-9]", "", cand)
    for vid in AGENT.fleet.ids():
        if vid in cand:
            return vid
    return None


@app.post("/api/photo")
async def photo(file: UploadFile = File(...), vehicle_id: str = Form(""), text: str = Form(""), uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    t0 = time.time()
    data = await file.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "photo too large")
    up = Path(S.uploads_dir); up.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time())}_{uuid.uuid4().hex[:6]}{Path(file.filename or 'p.jpg').suffix[:5]}"
    (up / name).write_bytes(data)  # stored locally only (152-FZ)
    mime = file.content_type or "image/jpeg"
    try:
        desc = AGENT.llm.vision("vision", VISION_PROMPT, base64.b64encode(data).decode(), mime)
    except LlmUnavailable as e:
        desc = ""
        STORE.audit(user_id=u["id"], role=u["role"], action="photo", output=f"vision unavailable: {e}", ok=0)
    plate = find_plate(desc) if desc else None
    res: dict = {"description": desc, "plate": plate, "stored": name}
    if vehicle_id and desc:
        c = AGENT.conv(u["id"], vehicle_id)
        turn = AGENT.handle(c, u, text or "Посмотри фото и подскажи, что проверить по руководству.", image_note=pii.mask(desc))
        _record(u, c, "photo", (text or "") + " [фото]", turn, t0, {"photo": name, "photo_desc": pii.mask(desc)[:1000]})
        res["turn"] = turn.as_dict()
    return res


@app.post("/api/stt")
async def stt(audio: UploadFile = File(...), lang: str = Form("ru"), uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    data = await audio.read()
    try:
        text, engine = voice.transcribe(data, audio.content_type or "audio/webm", LANGS[lang_or_default(lang)]["stt"], S)
    except voice.SttUnavailable as e:
        raise HTTPException(503, f"STT unavailable: {e}")
    STORE.audit(user_id=u["id"], role=u["role"], action="stt", input=f"{len(data)} bytes {lang}", output=pii.mask(text)[:500], tools=[{"tool": engine}], ok=1)
    return {"text": text, "engine": engine}  # audio itself is not stored


class TtsIn(BaseModel):
    text: str


@app.post("/api/tts")
def tts(body: TtsIn, uid: str | None = Cookie(None)):
    user_of(uid)
    try:
        wav = voice.silero_tts(re.sub(r"\[(S|X)\d+\]", "", body.text), S)
    except Exception:
        raise HTTPException(404, "server TTS disabled; use browser speech")
    return Response(wav, media_type="audio/wav")


class ConfirmIn(BaseModel):
    vehicle_id: str
    draft_id: str | None = None
    inspection_ok: bool = True


@app.post("/api/ticket/confirm")
def confirm(body: ConfirmIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    t0 = time.time()
    c = AGENT.conv(u["id"], body.vehicle_id)
    try:
        r = AGENT.confirm(c, body.draft_id, body.inspection_ok)
    except ServiceError as e:
        STORE.audit(user_id=u["id"], role=u["role"], vehicle_id=body.vehicle_id, action="ticket_confirm", output=f"{e.status} {e.detail[:300]}", ok=0)
        raise HTTPException(502, f"ТОиР: {e.status} {e.detail[:200]}")
    from ..agent.core import Turn

    t = Turn(text=r.get("text") or r.get("error", ""), cards=[r["card"]] if r.get("card") else [], tools=[{"tool": "toir.create_ticket", "ok": "ticket_id" in r, "ticket_id": r.get("ticket_id")}], ok="error" not in r)
    _record(u, c, "ticket_confirm" if body.inspection_ok else "inspection_not_confirmed", "", t, t0)
    return r


class RepairIn(BaseModel):
    vehicle_id: str
    action: str = "done"


@app.post("/api/repair/action")
def repair(body: RepairIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    c = AGENT.conv(u["id"], body.vehicle_id)
    r = AGENT.repair_action(c, body.action if body.action in ("done", "back", "finish") else "done")
    STORE.audit(user_id=u["id"], role=u["role"], vehicle_id=body.vehicle_id, action=f"repair_{body.action}", output=json.dumps(r, ensure_ascii=False), ok=int("error" not in r))
    return {"result": r, "card": AGENT.repair_card(body.vehicle_id)}


# ---------------------------------------------------------------- manuals: page text and image
def _page(doc_id: str, page: int):
    p = AGENT.index.get_page(doc_id, page)
    if p:
        return p, False
    if AGENT.extra:
        e = AGENT.extra.get_page(doc_id, page)
        if e:
            return e, True
    raise HTTPException(404, "page not found")


@app.get("/api/page/{doc_id}/{page}")
def page_json(doc_id: str, page: int, uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    p, extra = _page(doc_id, page)
    meta = AGENT.docs.get(doc_id, {})
    return {"doc_id": doc_id, "document": p.document, "full_title": meta.get("full_title", p.document), "page": page, "text": p.text, "extra": extra,
            "has_image": bool((not extra and (Path(S.manuals_dir) / meta.get("filename", "_")).exists() and shutil.which("pdftoppm")) or (extra and p.image))}


@app.get("/api/section/{doc_id}/{page}")
def section_json(doc_id: str, page: int, q: str = "", uid: str | None = Cookie(None)) -> dict:
    """Full text of the section/procedure containing the cited line, with its page span."""
    user_of(uid)
    p, extra = _page(doc_id, page)
    sec = AGENT.sections.find(doc_id, page, q)
    meta = AGENT.docs.get(doc_id, {})
    pdf = not extra and (Path(S.manuals_dir) / meta.get("filename", "_")).exists()
    imgs = bool((pdf and shutil.which("pdftoppm")) or (extra and p.image and (Path(S.extra_images_dir) / p.image).exists()))
    return {"document": p.document, "full_title": meta.get("full_title", p.document), "extra": extra, "pdf": pdf, "has_image": imgs,
            **(sec.as_dict() if sec else {"pages": [{"page": page, "text": p.text}], "start_page": page, "end_page": page, "span": str(page), "title": "", "label": ""})}


class ExtractIn(BaseModel):
    doc_id: str
    page: int
    q: str = ""
    question: str = ""


@app.post("/api/extract")
def extract(body: ExtractIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    _page(body.doc_id, body.page)
    return AGENT.section_extract(body.doc_id, body.page, body.q, pii.mask(body.question)[:300], LANGS[lang_or_default(u.get("lang"))]["llm"])


@app.get("/page/{doc_id}/{page}.png")
def page_png(doc_id: str, page: int, uid: str | None = Cookie(None)):
    user_of(uid)
    p, extra = _page(doc_id, page)
    if extra:
        if not p.image or not (Path(S.extra_images_dir) / p.image).exists():
            raise HTTPException(404)
        return FileResponse(Path(S.extra_images_dir) / p.image, media_type="image/jpeg")
    meta = AGENT.docs[doc_id]
    pdf = Path(S.manuals_dir) / meta["filename"]
    out = Path(S.page_cache_dir) / f"{doc_id}-{page}.png"
    if not out.exists():
        if not pdf.exists() or not shutil.which("pdftoppm"):
            raise HTTPException(404, "PDF or renderer missing")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", str(page), "-l", str(page), "-singlefile", str(pdf), str(out.with_suffix(""))], check=True, timeout=60)
    return FileResponse(out, media_type="image/png")


@app.get("/manuals/{doc_id}.pdf")
def manual_pdf(doc_id: str, uid: str | None = Cookie(None)):
    user_of(uid)
    meta = AGENT.docs.get(doc_id)
    if not meta or not (Path(S.manuals_dir) / meta["filename"]).exists():
        raise HTTPException(404)
    return FileResponse(Path(S.manuals_dir) / meta["filename"], media_type="application/pdf")


# ---------------------------------------------------------------- audit / ops
@app.get("/api/audit")
def audit(limit: int = 200, uid: str | None = Cookie(None)) -> dict:
    user_of(uid, manager=True)
    rows = STORE.q("SELECT a.*, u.name AS user_name FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT ?", (min(limit, 2000),))
    for r in rows:
        r["user_id"] = (r["user_id"] or "")[:8]
        for k in ("tools", "sources"):
            try:
                r[k] = json.loads(r[k]) if r[k] else []
            except json.JSONDecodeError:
                pass
    return {"rows": rows}


@app.get("/api/audit.csv")
def audit_csv(uid: str | None = Cookie(None)):
    user_of(uid, manager=True)
    rows = STORE.q("SELECT id, datetime(ts,'unixepoch','localtime') AS time, substr(user_id,1,8) AS user, role, vehicle_id, action, input, output, sources, latency_ms, tokens, ok FROM audit ORDER BY id DESC LIMIT 5000")
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit.csv"})


@app.get("/api/health")
def health() -> dict:
    return {"ml": AGENT.ml.health(), "toir": AGENT.toir.health(), "llm_configured": AGENT.llm.available, "model": S.llm_model,
            "pages": len(AGENT.index.pages), "extra_pages": len(AGENT.extra.pages) if AGENT.extra else 0,
            "stt": "+".join(x for x in ("whisper-local" if S.speech_url else "", "deepgram" if S.deepgram_api_key else "") if x) or "browser",
            "tts": "silero" if (S.speech_url or S.silero_enabled) else "browser", "tracing": "opik" if AGENT.llm._opik else None}


@app.get("/api/usage")
def usage(uid: str | None = Cookie(None)) -> dict:
    user_of(uid, manager=True)
    return AGENT.llm.usage.as_dict()


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    jlog(event="error", path=request.url.path, error=type(exc).__name__, detail=str(exc)[:300])
    return JSONResponse(status_code=500, content={"detail": "internal error"})
