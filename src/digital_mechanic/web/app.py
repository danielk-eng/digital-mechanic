"""FastAPI app: mechanic chat (mobile), chief mechanic dashboard, voice, photos, audit, 152-FZ."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import csv
import io
import html
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import media as umedia, pii, privacy_laws, voice
from ..translate import Translator
from ..agent.llm import REQUEST_COUNTRY, REQUEST_PRIMARY
from .. import tenancy
from ..replica import Replica
from ..agent import profile as uprofile, style as ustyle, memory as umemory
from ..agent.briefing import Briefing
from .. import demo_seed
from ..agent.llm import LlmUnavailable
from ..agent.prompts import VISION_PROMPT
from ..bootstrap import build_agent
from ..clients import ServiceError
from ..config import get_settings
from ..history import mask_name, msk_time
from ..i18n import LANGS, answer_lang, lang_or_default

S = get_settings()
AGENT = build_agent(S)
TR = Translator(AGENT.llm, AGENT.store, os.environ.get("I18N_DIR", "data/i18n"))
for _lang, _pages in TR.pages.items():  # human translations of the manuals: search in the user's language, cite the original page
    AGENT.index.add_translations(_lang, _pages)
STORE = AGENT.store
REPLICA = Replica(STORE)  # Vercel: mirror companies/people/QR links through a private Blob
tenancy.ensure_seed(STORE)
AGENT.fleet = tenancy.ScopedFleet(AGENT.fleet)  # each request sees only its company's trucks (engineer: all)
DEMO = demo_seed.load(os.environ.get("DEMO_USERS", "data/demo_users.json"))
AGENT.briefing = Briefing(AGENT, DEMO)
try:
    demo_seed.seed(STORE, AGENT.fleet, DEMO)  # demo accounts + who scanned which truck (idempotent)
except Exception as _e:  # noqa: BLE001 - the stand must start even if the fleet stub is down
    print("demo seed skipped:", type(_e).__name__)
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Цифровой механик", version="1.0")


class TextGZip:
    """Gzip only text (HTML/JS/CSS/JSON): PDFs, page images and audio pass through untouched (range requests, streaming)."""

    SKIP = (".pdf", ".png", ".jpg", ".jpeg", ".woff2", ".wav", ".mp3")

    def __init__(self, app_):
        self.app, self.gz = app_, GZipMiddleware(app_, minimum_size=1000)

    async def __call__(self, scope, receive, send):
        p = scope.get("path", "") if scope["type"] == "http" else ""
        if scope["type"] != "http" or p.endswith(self.SKIP) or p.startswith(("/api/chat/stream", "/api/tts", "/api/stt", "/api/photo", "/api/media", "/page/", "/manuals/")):
            return await self.app(scope, receive, send)
        return await self.gz(scope, receive, send)


app.add_middleware(TextGZip)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
Path(S.log_path).parent.mkdir(parents=True, exist_ok=True)


def jlog(**kw) -> None:
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(S.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


# Signed profile cookie: on serverless stands (Vercel) every instance has its own /tmp DB, so a session created on one
# instance is unknown on the next. The cookie carries the user's own profile (no other data), HMAC-signed with a key
# derived from the stand's secret; an unknown uid is restored from it.
_PROFILE_KEYS = ("name", "role", "lang", "experience", "detail", "answer_lang", "big_font", "voice_out", "consent_at", "country", "company_id")


def _cookie_key() -> bytes:
    base = os.environ.get("SESSION_SECRET") or S.api_key() or "dm-local"
    return hashlib.sha256(("dm-profile-cookie:" + base).encode()).digest()


def profile_cookie(u: dict) -> str:
    raw = json.dumps({"id": u["id"], **{k: u.get(k) for k in _PROFILE_KEYS if k in u}}, ensure_ascii=False, separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return b + "." + hmac.new(_cookie_key(), b.encode(), hashlib.sha256).hexdigest()[:32]


def draft_token(uid: str, vid: str, draft: dict) -> str:
    """Vercel: the ticket draft lives in one instance's memory, «Подтвердить» may land on another. The card carries the
    draft HMAC-signed for this user and vehicle; the confirming instance restores it (no client-made drafts: the signature)."""
    b = base64.urlsafe_b64encode(json.dumps(draft, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    return b + "." + hmac.new(_cookie_key(), f"draft:{uid}:{vid}:{b}".encode(), hashlib.sha256).hexdigest()[:40]


def draft_from_token(uid: str, vid: str, tok: str | None) -> dict | None:
    if not tok or "." not in tok:
        return None
    b, sig = tok.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_cookie_key(), f"draft:{uid}:{vid}:{b}".encode(), hashlib.sha256).hexdigest()[:40]):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
    except Exception:
        return None


def _sign_draft_cards(out: dict, uid: str, c) -> dict:
    for cd in out.get("cards") or []:
        if cd.get("kind") == "ticket_draft" and c.draft and cd.get("draft_id") == c.draft.get("id") and c.vehicle:
            cd["token"] = draft_token(uid, c.vehicle.vehicle_id, c.draft)
    out["draft_open"] = bool(c.draft)  # the client forgets its signed draft once it is sent or cancelled
    return out


def restore_from_cookie(uid: str | None, up: str | None) -> dict | None:
    if not uid or not up or "." not in up:
        return None
    b, sig = up.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_cookie_key(), b.encode(), hashlib.sha256).hexdigest()[:32]):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
    except Exception:
        return None
    if d.get("id") != uid:
        return None
    return STORE.upsert_user(uid, **{k: d[k] for k in _PROFILE_KEYS if k in d and d[k] is not None})


def set_profile_cookie(response: Response, u: dict) -> None:
    response.set_cookie("up", profile_cookie(u), httponly=True, samesite="lax", max_age=400 * 86400)


# Login session: the PIN is asked once. Cookie «s» = signed {uid, sid, iat}, httpOnly, ~400 days (the browser maximum),
# renewed on every boot (/api/session), so it lasts until an explicit logout. Server side: table sessions (sid -> user,
# revoked). On a serverless stand an instance that has not seen the sid yet accepts the signature and records it; logout
# revokes the sid where it is recorded and deletes the cookie. A plain «uid» cookie is ignored (it was forgeable).
SESSION_MAX_AGE = 400 * 86400


def session_cookie(uid: str, sid: str | None = None) -> str:
    raw = json.dumps({"u": uid, "s": sid or uuid.uuid4().hex, "t": int(time.time())}, separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return b + "." + hmac.new(_cookie_key(), ("s:" + b).encode(), hashlib.sha256).hexdigest()[:40]


def parse_session(value: str | None) -> dict | None:
    if not value or "." not in value:
        return None
    b, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_cookie_key(), ("s:" + b).encode(), hashlib.sha256).hexdigest()[:40]):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
    except Exception:
        return None
    return d if d.get("u") and d.get("s") else None


def start_session(response: Response, uid: str) -> None:
    sid = uuid.uuid4().hex
    STORE.x("INSERT OR REPLACE INTO sessions(sid,user_id,created,seen) VALUES(?,?,?,?)", (sid, uid, time.time(), time.time()))
    response.set_cookie("s", session_cookie(uid, sid), httponly=True, samesite="lax", max_age=SESSION_MAX_AGE,
                        secure=bool(os.environ.get("VERCEL")))


def end_session(request, response: Response) -> None:
    d = parse_session(request.cookies.get("s"))
    if d:
        STORE.x("INSERT INTO sessions(sid,user_id,created,seen,revoked) VALUES(?,?,?,?,?) ON CONFLICT(sid) DO UPDATE SET revoked=excluded.revoked",
                (d["s"], d["u"], time.time(), time.time(), time.time()))
    for k in ("s", "uid", "up"):
        response.delete_cookie(k)


def _session_uid(request) -> str | None:
    d = parse_session(request.cookies.get("s"))
    if not d:
        return None
    row = STORE.one("SELECT revoked FROM sessions WHERE sid=?", (d["s"],))
    if row is None:  # another instance (Vercel) or a DB from before the redeploy: the signature is the proof
        STORE.x("INSERT OR IGNORE INTO sessions(sid,user_id,created,seen) VALUES(?,?,?,?)", (d["s"], d["u"], d.get("t") or time.time(), time.time()))
    elif row["revoked"]:
        return None
    return d["u"]


@app.middleware("http")
async def _restore_session(request, call_next):
    write = request.method not in ("GET", "HEAD", "OPTIONS")
    if REPLICA.on and (request.url.path.startswith(("/api/", "/q/", "/manager", "/engineer")) or request.url.path == "/"):
        await asyncio.to_thread(REPLICA.pull, write)  # serverless: shared companies/people/QR links (no-op on the deb stand)
    uid = _session_uid(request)
    up = request.cookies.get("up")
    if uid and up and not STORE.user(uid):
        restore_from_cookie(uid, up)
    # downstream endpoints read Cookie «uid»: replace whatever the browser sent with the verified one (or nothing)
    hdr = [(k, v) for k, v in request.scope["headers"] if k != b"cookie"]
    rest = "; ".join(f"{k}={v}" for k, v in request.cookies.items() if k != "uid")
    cookie = "; ".join(x for x in (rest, f"uid={uid}" if uid else "") if x)
    if cookie:
        hdr.append((b"cookie", cookie.encode("latin-1", "ignore")))
    request.scope["headers"] = hdr
    me = STORE.user(uid) if uid else None
    REQUEST_COUNTRY.set((me or {}).get("country"))  # LLM provider by the person's country (152-FZ: RU -> Cloud.ru)
    co = tenancy.company_of(me) if me else None
    tenancy.SCOPE.set(tenancy.scope_for(STORE, AGENT.fleet.all_ids(), co) if me else None)
    crow = STORE.one("SELECT llm_primary FROM companies WHERE id=?", (co,)) if co else None
    REQUEST_PRIMARY.set((crow or {}).get("llm_primary") or None)  # the engineer may switch the provider per company
    response = await call_next(request)
    if REPLICA.on and request.url.path.startswith(("/api/", "/q/")):
        await asyncio.to_thread(REPLICA.push)  # only uploads when the mirrored tables changed (incl. a user restored from cookie)
    if uid and request.url.path == "/api/session":  # sliding renewal: active users are never asked for the PIN again
        response.set_cookie("s", request.cookies["s"], httponly=True, samesite="lax", max_age=SESSION_MAX_AGE,
                            secure=bool(os.environ.get("VERCEL")))
    return response


def user_of(uid: str | None, manager: bool = False, roles: tuple[str, ...] | None = None) -> dict:
    """RBAC on the server: every endpoint states who may call it; the UI only hides what the role cannot do."""
    u = STORE.user(uid) if uid else None
    if not u:
        raise HTTPException(401, "login required")
    if manager and u["role"] != "manager":
        raise HTTPException(403, "manager only")
    if roles and u["role"] not in roles:
        raise HTTPException(403, f"{'/'.join(roles)} only")
    if not u.get("active", 1):
        raise HTTPException(401, "account deactivated")
    return u


def pub(u: dict | None) -> dict | None:
    """User row for the browser: never the PIN hash; «chief» tells the UI whether the staff tab is allowed."""
    if not u:
        return u
    d = {k: v for k, v in u.items() if k != "pin_hash"}
    d["chief"] = bool(u.get("role") == "manager" and (u.get("login") or u.get("id") in {a["uid"] for a in DEMO["accounts"]}))
    return d


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
def privacy(country: str = "", uid: str | None = Cookie(None)) -> str:
    """Country-specific: the law, where data is stored, localization and how speech is recognised."""
    u = STORE.user(uid) if uid else None
    c = privacy_laws.COUNTRIES[privacy_laws.country(country or (u or {}).get("country"))]
    where = ("При внедрении сервер и база данных размещаются на территории страны (требование локализации: " + c["localize_note"] + ")."
             if c["localize"] else "Требование локализации: " + c["localize_note"] + ". Сервер размещается по выбору оператора с соблюдением правил трансграничной передачи.")
    speech = ("речь распознаётся локально на сервере (Whisper), аудио не сохраняется и наружу не уходит; облачное распознавание — только если "
              "локальное недоступно." if c["localize"] else "речь распознаётся на сервере (Whisper) или в облачном сервисе Deepgram (без использования аудио для обучения); аудио не сохраняется.")
    return ((STATIC / "privacy.html").read_text(encoding="utf-8").replace("{{LAW}}", c["law"]).replace("{{WHERE}}", where)
            .replace("{{SPEECH}}", speech).replace("{{COUNTRY}}", c["name"]["ru"]))


@app.get("/api/countries")
def countries(lang: str = "ru") -> dict:
    return {"default": privacy_laws.DEFAULT, "countries": privacy_laws.public_list(lang_or_default(lang))}


# ---------------------------------------------------------------- auth / profile
class LoginIn(BaseModel):
    name: str
    role: str = "mechanic"
    lang: str = "ru"
    experience: str = "experienced"
    consent: bool = False
    country: str = "RU"


@app.post("/api/login")
def login(body: LoginIn, response: Response) -> dict:
    """Name-only login is removed: every role signs in with a login code and a PIN (/api/login/demo).
    Accounts are the demo ones (PIN on the login screen) or employees the chief created."""
    STORE.audit(user_id=None, role=None, action="login_name_refused", ok=0)
    raise HTTPException(410, "login by name is disabled: use login code + PIN (/api/login/demo)")


class DemoLoginIn(BaseModel):
    account: str
    pin: str
    lang: str = "ru"
    consent: bool = False
    country: str = "RU"


@app.get("/api/demo/accounts")
def demo_accounts() -> dict:
    """Login screen: demo accounts with their demo PINs (no other secrets)."""
    return {"accounts": demo_seed.public_accounts(DEMO)}


def _pin_hash(pin: str, salt: str | None = None) -> str:
    salt = salt or uuid.uuid4().hex[:16]
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()


def _pin_ok(pin: str, stored: str | None) -> bool:
    return bool(stored and "$" in stored and hmac.compare_digest(_pin_hash(pin, stored.split("$", 1)[0]), stored))


@app.post("/api/login/demo")
def login_demo(body: DemoLoginIn, response: Response) -> dict:
    """Login by code + PIN: demo accounts (PIN shown on the login screen) and employees the chief created."""
    code = body.account.strip().lower()[:40]
    db = STORE.one("SELECT * FROM users WHERE login=?", (code,))
    if db and db.get("pin_hash"):
        if not _pin_ok(body.pin, db["pin_hash"]):
            STORE.audit(user_id=None, role=None, action="login_pin", input=code, ok=0)
            raise HTTPException(403, "wrong PIN")
        if not db.get("active", 1):
            raise HTTPException(403, "account deactivated")
        if S.require_consent and not body.consent:
            raise HTTPException(400, f"consent required ({privacy_laws.COUNTRIES[privacy_laws.country(body.country)]['short']})")
        u = STORE.upsert_user(db["id"], consent_at=time.time(), **({"lang": lang_or_default(body.lang)} if db.get("onboarded") else {}))
        start_session(response, db["id"])
        set_profile_cookie(response, u)
        STORE.audit(user_id=db["id"], role=db["role"], action="login_pin", ok=1)
        return {"user": pub(u), "account": {"key": code, "title": db.get("title"), "vehicles": json.loads(db.get("vehicles") or "[]")}}
    a = demo_seed.account(DEMO, code)
    if not a or body.pin != a["pin"]:
        STORE.audit(user_id=None, role=None, action="login_demo", input=code, ok=0)
        raise HTTPException(403, "wrong PIN")
    if db and not db.get("active", 1):
        raise HTTPException(403, "account deactivated")
    if S.require_consent and not body.consent:
        raise HTTPException(400, f"consent required ({privacy_laws.COUNTRIES[privacy_laws.country(body.country)]['short']})")
    if not STORE.user(a["uid"]):
        demo_seed.seed(STORE, AGENT.fleet, DEMO)
    u = STORE.upsert_user(a["uid"], lang=lang_or_default(body.lang), consent_at=time.time(), country=privacy_laws.country(body.country), role=a["role"])
    start_session(response, a["uid"])
    set_profile_cookie(response, u)
    STORE.audit(user_id=a["uid"], role=a["role"], action="login_demo", ok=1)
    return {"user": pub(u), "account": {k: a.get(k) for k in ("key", "title", "vehicles")}}


@app.get("/api/session")
def session(uid: str | None = Cookie(None)) -> dict:
    """Boot check without a 401 in the browser console: {"user": null} when not logged in."""
    u = STORE.user(uid) if uid else None
    # server_tts: false -> the browser speaks itself (no POST /api/tts that can only answer 404)
    # server_stt: false -> the browser recognises speech itself (Web Speech API) instead of POST /api/stt that can only answer 503
    return {"user": pub(u), "langs": {k: v["name"] for k, v in LANGS.items()}, "server_tts": bool(S.speech_url or S.silero_enabled),
            "server_stt": voice.stt_configured(S)}


@app.get("/api/me")
def me(uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    p = uprofile.load(u)
    acc = next((a for a in DEMO["accounts"] if a["uid"] == u["id"]), None)
    return {"user": pub(u), "profile": uprofile.public(p), "style": ustyle.public(p, u), "langs": {k: v["name"] for k, v in LANGS.items()},
            "account": {k: acc.get(k) for k in ("key", "title", "vehicles")} if acc else None}


class StyleIn(BaseModel):
    text: str | None = None
    memory: bool | None = None
    reset: bool = False


@app.post("/api/me/style")
def edit_style(body: StyleIn, uid: str | None = Cookie(None)) -> dict:
    """Settings: «Как помощник с вами говорит» — the user reads and edits his style card; memory callbacks on/off."""
    u = user_of(uid)
    p = uprofile.load(u)
    if body.reset:
        p.pop("style", None)
    if body.text is not None:
        ustyle.set_text(p, u, pii.mask(body.text), "user")
    if body.memory is not None:
        p["memory"] = bool(body.memory)
    STORE.upsert_user(u["id"], profile=uprofile.dump(p))
    return {"style": ustyle.public(p, u)}


class ProfileEditIn(BaseModel):
    remove_term: str | None = None
    reset: bool = False


@app.post("/api/me/profile")
def edit_profile(body: ProfileEditIn, uid: str | None = Cookie(None)) -> dict:
    """Settings: the user sees what the assistant learned about him and can remove a term or reset everything."""
    u = user_of(uid)
    p = uprofile.load({"profile": ""} if body.reset else u)
    if body.remove_term:
        p["terms"].pop(body.remove_term, None)
        p["inferred"] = uprofile.inferred_level(p)
    STORE.upsert_user(u["id"], profile=uprofile.dump(p))
    return {"profile": uprofile.public(p)}


EXPERIENCE_LEVELS = ("novice", "experienced", "expert")
DETAIL_LEVELS = ("brief", "detailed")


class ProfileIn(BaseModel):
    lang: str | None = None
    experience: str | None = None
    detail: str | None = None
    answer_lang: str | None = None
    big_font: bool | None = None
    voice_out: bool | None = None
    country: str | None = None


@app.post("/api/me")
def update_me(body: ProfileIn, response: Response, uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    kw = {k: v for k, v in body.model_dump().items() if v is not None}
    if "lang" in kw:
        kw["lang"] = lang_or_default(kw["lang"])
    if "answer_lang" in kw and kw["answer_lang"] not in LANGS:
        kw["answer_lang"] = ""  # same as interface
    if kw.get("experience") not in (None, *EXPERIENCE_LEVELS):
        raise HTTPException(422, "experience: novice | experienced | expert")
    if kw.get("detail") not in (None, *DETAIL_LEVELS):
        raise HTTPException(422, "detail: brief | detailed")
    if "country" in kw:
        kw["country"] = privacy_laws.country(kw["country"])
    for k in ("big_font", "voice_out"):
        if k in kw:
            kw[k] = int(kw[k])
    u = STORE.upsert_user(uid, **kw)
    set_profile_cookie(response, u)
    return {"user": pub(u)}


@app.delete("/api/me")
def delete_me(response: Response, uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    for f in STORE.media_files_of(uid):  # photos, frames and recordings are personal data too
        (Path(S.uploads_dir) / Path(f).name).unlink(missing_ok=True)
    STORE.delete_user_data(uid)
    for k in ("s", "uid", "up"):
        response.delete_cookie(k)
    return {"deleted": True}


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    end_session(request, response)
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
    ov = AGENT.fleet_overview()
    mech = _mechanics_by_vehicle()
    for r in ov["vehicles"]:  # for the chief's filters: brand, assigned mechanics
        v = AGENT.fleet.get(r["vehicle_id"])
        r["make"] = v.make if v else None
        r["mechanics"] = mech.get(r["vehicle_id"], [])
    return ov


def _mechanics_by_vehicle() -> dict[str, list[str]]:
    """Mechanics assigned to each truck: demo accounts and accounts the chief created (their truck list) + open assignments."""
    out: dict[str, list[str]] = {}
    add = lambda vid, name: name and name not in out.setdefault(vid, []) and out[vid].append(name)  # noqa: E731
    for a in DEMO.get("accounts", []):
        if a.get("role") == "mechanic":
            for vid in a.get("vehicles") or []:
                add(vid, a.get("name"))
    for u in STORE.q("SELECT id, name, vehicles FROM users WHERE role='mechanic' AND vehicles IS NOT NULL AND COALESCE(active, 1)=1"):
        try:
            for vid in json.loads(u["vehicles"] or "[]"):
                add(vid, u["name"])
        except (ValueError, TypeError):
            pass
    names = {u["id"]: u["name"] for u in STORE.q("SELECT id, name FROM users")} | {a["uid"]: a["name"] for a in DEMO.get("accounts", [])}
    for h in STORE.handoffs(None):
        p = h.get("payload") or {}
        if h.get("kind") == "assign" and h.get("status") != "closed" and h.get("vehicle_id"):
            add(h["vehicle_id"], names.get(p.get("assignee_uid")))
    return out


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
                output=turn.text[:4000], tools=turn.tools, sources=[f"{s.get('document')} стр.{s.get('page')}" for s in turn.sources], latency_ms=lat, tokens=turn.tokens, ok=int(turn.ok))
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
    mine = set(AGENT.fleet.ids())
    for t in STORE.tickets():
        if t["vehicle_id"] not in mine:
            continue
        p = t["payload"]
        out.append({"ticket_id": t["ticket_id"], "vehicle_id": t["vehicle_id"], "urgency": t["urgency"], "anomaly_type": t["anomaly_type"],
                    "at": msk_time(t["ts"]), "ts": t["ts"], "conclusion": p.get("conclusion", ""),
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
    row = STORE.one("SELECT vehicle_id FROM tickets WHERE ticket_id=?", (ticket_id,))
    if not row or row["vehicle_id"] not in AGENT.fleet.ids():
        raise HTTPException(404)
    rev = {"decision": body.decision, "comment": pii.mask(body.comment)[:500], "by": mask_name(u.get("name") or ""), "at": time.time()}
    STORE.set_ticket_review(ticket_id, rev, body.decision)
    STORE.audit(user_id=u["id"], role=u["role"], action="ticket_review", input=f"{ticket_id} {body.decision}", output=rev["comment"], ok=1)
    return {"ok": True, "review": rev}


@app.get("/api/handoffs")
def handoffs(status: str | None = None, uid: str | None = Cookie(None)) -> dict:
    me_ = user_of(uid, manager=True)
    mine, co = set(AGENT.fleet.ids()), tenancy.company_of(me_)
    staff = {u["id"] for u in STORE.q("SELECT id, company_id, role FROM users") if tenancy.company_of(u) == co}
    rows = [r for r in STORE.handoffs(status) if ((r.get("vehicle_id") in mine) if r.get("vehicle_id") else (r.get("user_id") in staff))]
    # the chief sees his own staff by name (as on the «Сотрудники» tab); the name is the account name, not free text
    who = {u["id"]: u for u in STORE.q("SELECT id, name, title, role FROM users")}
    for r in rows:
        u = who.get(r.get("user_id")) or {}
        r["user_name"] = (u.get("name") or r.get("user_name") or "—") + (f", {u['title']}" if u.get("title") else "")
    return {"handoffs": rows}


class ResolveIn(BaseModel):
    status: str
    comment: str = ""


@app.post("/api/handoff/{hid}")
def resolve_handoff(hid: int, body: ResolveIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid, manager=True)
    if body.status not in ("taken", "closed", "open"):
        raise HTTPException(400, "status")
    h = STORE.one("SELECT vehicle_id FROM handoffs WHERE id=?", (hid,))
    if not h or (h["vehicle_id"] and h["vehicle_id"] not in AGENT.fleet.ids()):
        raise HTTPException(404)
    STORE.resolve_handoff(hid, u["id"], body.status, pii.mask(body.comment)[:500])
    return {"ok": True}


@app.get("/api/photo/{name}")
def photo_file(name: str, uid: str | None = Cookie(None)):
    u = user_of(uid)
    if not re.fullmatch(r"[\w.-]{3,80}", name) or not (Path(S.uploads_dir) / name).is_file():
        raise HTTPException(404)
    if u["role"] != "engineer":  # own uploads; the chief: uploads about his company's trucks or by his people (other companies: 404)
        rows = STORE.q("SELECT user_id, vehicle_id FROM media WHERE files LIKE ?", (f'%"{name}"%',))
        mine = any(r["user_id"] == u["id"] for r in rows)
        if not mine and u["role"] == "manager":
            fleet, staff = set(AGENT.fleet.ids()), _staff_ids(tenancy.company_of(u))
            mine = any(r["vehicle_id"] in fleet or r["user_id"] in staff for r in rows)
        if not mine:
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
    sources: list[dict] | None = None  # «Подробнее»: the brief answer's sources (labels -> pages), see Agent.rehydrate_frags
    session_id: str | None = None  # the check last diagnosed in this dialog (another instance may not have it), see Agent.restore_check
    draft_token: str | None = None  # the signed ticket draft shown in this dialog («Подтверждаю заявку» by voice/text on another instance)
    prefetch: bool = False  # «Подробнее» written ahead of the click: not stored, comes back signed (see expand_sig)
    ready: dict | None = None  # the click on a prefetched «Подробнее»: {text, sources, sig} -> stored as is, no LLM call


def expand_sig(uid: str, vid: str | None, text: str, sources: list | None) -> str:
    """A prefetched «Подробнее» is stored only on the click; the client sends it back, the signature proves it is ours."""
    raw = json.dumps([text, [[s.get("label"), s.get("doc_id"), s.get("page")] for s in (sources or [])]], ensure_ascii=False)
    return hmac.new(_cookie_key(), f"expand:{uid}:{vid or '-'}:{raw}".encode(), hashlib.sha256).hexdigest()[:40]


@app.post("/api/chat")
def chat(body: ChatIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    t0 = time.time()
    try:
        c = AGENT.conv(u["id"], body.vehicle_id)
    except KeyError:
        raise HTTPException(404, "unknown vehicle")
    if c.vehicle is None and u["role"] not in ("manager", "engineer"):
        raise HTTPException(400, "choose a vehicle")
    vid = c.vehicle.vehicle_id if c.vehicle else None
    if body.event == "expand" and body.ready:
        r = body.ready
        txt, srcs = str(r.get("text") or ""), list(r.get("sources") or [])
        if not txt or not hmac.compare_digest(str(r.get("sig") or ""), expand_sig(u["id"], vid, txt, srcs)):
            raise HTTPException(403, "bad prefetch signature")
        from ..agent.core import Turn

        turn = Turn(text=txt, sources=srcs, tools=[{"tool": "expand_prefetched"}])
        _record(u, c, "expand", "", turn, t0)
        return {**turn.as_dict(), "latency_ms": int((time.time() - t0) * 1000)}
    if body.event == "expand" and body.prefetch:
        prof = uprofile.load(u)
        level = u.get("experience") or "experienced"
        turn = AGENT.handle(c, {**u, "_vocab_hint": uprofile.prompt_hint(prof, level), "_style": ustyle.prompt(prof, u), "_memory": ""},
                            body.text[:4000], event="expand", sources=body.sources, session_id=body.session_id, detached=True)
        turn.text, _ = uprofile.gloss(turn.text, prof, level)
        out = {**turn.as_dict(), "latency_ms": int((time.time() - t0) * 1000), "prefetched": True}
        if turn.ok and turn.text:
            out["sig"] = expand_sig(u["id"], vid, turn.text, turn.sources)
        return out
    if c.draft is None and body.draft_token and c.vehicle:
        d = draft_from_token(u["id"], c.vehicle.vehicle_id, body.draft_token)
        if d:
            with c.lock:
                c.draft = d
                AGENT.restore_check(c, (d.get("request") or {}).get("session_id"))
    # adaptive profile: learn the mechanic's terms, answer in them; novices get short glosses the first few times
    prof = uprofile.load(u)
    if u["role"] == "mechanic":
        uprofile.update_from_message(prof, body.text or "", body.event)
    level = u.get("experience") or "experienced"
    mem, recalled = ("", []) if c.recalled else umemory.hint(STORE, DEMO, u, c.vehicle.vehicle_id if c.vehicle else None, prof.get("memory", True), question=None if body.event else body.text)
    turn = AGENT.handle(c, {**u, "_vocab_hint": uprofile.prompt_hint(prof, level), "_style": ustyle.prompt(prof, u), "_memory": mem},
                        body.text[:4000], event=body.event, sources=body.sources if body.event == "expand" else None, session_id=body.session_id)
    if recalled:
        c.recalled = True
        turn.tools.append({"tool": "memory.recent_work", "ok": True, "items": [w.get("ref") or w["what"] for w in recalled]})
    turn.text, glossed = uprofile.gloss(turn.text, prof, level)
    if level != "novice":  # an experienced mechanic does not need "ГБЦ -> головка блока цилиндров" explained back to him
        turn.understood = [p for p in (turn.understood or []) if not uprofile.terms_in(p[0])]
    if not body.event and body.text:
        recent = [m["content"] for m in STORE.q("SELECT content FROM messages WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT 12", (u["id"],))][::-1] + [body.text]
        if u["role"] != "mechanic":
            prof["messages"] = prof.get("messages", 0) + 1
        threading.Thread(target=_evolve_style, args=(u["id"], recent), daemon=True).start() if (prof.get("messages", 0) % ustyle.EVOLVE_EVERY == 0) else None
    STORE.upsert_user(u["id"], profile=uprofile.dump(prof))
    # "expand" re-renders the previous answer: do not store it again as a user message
    shown = {"expand": "", "alternatives": "Другие варианты"}.get(body.event or "", None)
    _record(u, c, "chat" if not body.event else body.event, body.text if shown is None else shown, turn, t0)
    return _sign_draft_cards({**turn.as_dict(), "latency_ms": int((time.time() - t0) * 1000)}, u["id"], c)


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn, uid: str | None = Cookie(None)):
    """Same as /api/chat, streamed as NDJSON: {"step"} / {"draft"} lines while the model writes, then {"turn"} (or
    {"error", "status"}). On Vercel the /api/progress poll lands on other instances and sees nothing; this stream
    carries the live lines of the SAME instance (used by «Подробнее», 8–17 s there)."""
    from fastapi.responses import StreamingResponse

    u = user_of(uid)  # 401 before the stream starts
    cid = f"{u['id']}:{body.vehicle_id or '-'}"
    res: dict = {}

    def run() -> None:
        try:
            res["turn"] = chat(body, uid)
        except HTTPException as e:
            res["error"], res["status"] = str(e.detail)[:300], e.status_code
        except Exception as e:  # noqa: BLE001 - reported to the client, which falls back to /api/chat
            res["error"], res["status"] = type(e).__name__, 500

    th = threading.Thread(target=run, daemon=True, name=f"chat-stream-{cid}")
    th.start()

    def gen():
        last_step, last_draft = None, None
        yield json.dumps({"step": PROGRESS_START}, ensure_ascii=False) + "\n"
        while th.is_alive():
            th.join(0.3)
            steps, draft = AGENT.progress.get(cid) or [], AGENT.drafts.get(cid, "")
            if steps and steps[-1] != last_step:
                last_step = steps[-1]
                yield json.dumps({"step": last_step}, ensure_ascii=False) + "\n"
            if draft and draft != last_draft:
                last_draft = draft
                yield json.dumps({"draft": draft}, ensure_ascii=False) + "\n"
        yield json.dumps({"turn": res["turn"]} if "turn" in res else {"error": res.get("error", "error"), "status": res.get("status", 500)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


PROGRESS_START = "📖 Перечитываю страницы руководства"


def _evolve_style(uid: str, recent: list[str]) -> None:
    u = STORE.user(uid)
    if not u:
        return
    p = uprofile.load(u)
    if ustyle.maybe_evolve(AGENT.llm, p, u, recent):
        STORE.upsert_user(uid, profile=uprofile.dump(p))


@app.get("/api/progress")
def progress(vehicle_id: str | None = None, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    cid = f"{u['id']}:{vehicle_id or '-'}"
    return {"steps": AGENT.progress.get(cid, []), "draft": AGENT.drafts.get(cid, "")}


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
        STORE.add_media(u["id"], vehicle_id, "photo", [name], pii.mask(desc)[:1500], None, pii.mask(text)[:500])
        res["turn"] = turn.as_dict()
    return res


MEDIA_ASK = {"photo": "Посмотри фото и подскажи, что проверить по руководству.",
             "video": "Посмотри кадры видео (и звук, если он есть) и подскажи, что проверить по руководству.",
             "sound": "Я записал звук двигателя на телефон. Что по нему проверить по руководству?"}


def _vision(u: dict, data: bytes, mime: str) -> str:
    try:
        return AGENT.llm.vision("vision", VISION_PROMPT, base64.b64encode(data).decode(), mime)
    except LlmUnavailable as e:
        STORE.audit(user_id=u["id"], role=u["role"], action="vision", output=f"vision unavailable: {e}", ok=0)
        return ""


@app.post("/api/media")
async def media_upload(kind: str = Form(...), vehicle_id: str = Form(...), text: str = Form(""), times: str = Form(""),
                       frames: list[UploadFile] | None = File(None), audio: UploadFile | None = File(None),
                       uid: str | None = Cookie(None)) -> dict:
    """Photo / short video / engine sound from the phone, tied to the vehicle and its history.

    Video is cut on the phone: up to 4 keyframes (JPEG) plus the audio track as 16 kHz mono WAV, so a 50 MB clip
    never has to travel over a weak shop Wi-Fi. Sound = WAV of the recording. The raw clip is not stored."""
    from concurrent.futures import ThreadPoolExecutor
    u = user_of(uid)
    t0 = time.time()
    if kind not in MEDIA_ASK:
        raise HTTPException(400, "kind must be photo, video or sound")
    c = _conv_for(u, vehicle_id)
    frames = (frames or [])[:4]
    if kind in ("photo", "video") and not frames:
        raise HTTPException(400, "no frames")
    if kind == "sound" and not audio:
        raise HTTPException(400, "no audio")
    up = Path(S.uploads_dir); up.mkdir(parents=True, exist_ok=True)
    stem = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    imgs, files = [], []
    for i, f in enumerate(frames):
        data = await f.read()
        if len(data) > 12 * 1024 * 1024:
            raise HTTPException(413, "frame too large")
        name = f"{stem}_{i}{Path(f.filename or 'f.jpg').suffix[:5] or '.jpg'}"
        (up / name).write_bytes(data)  # stored on the stand only
        imgs.append((data, f.content_type or "image/jpeg")); files.append(name)
    tl = [x for x in times.split(",") if x][:len(imgs)]
    with ThreadPoolExecutor(4) as ex:
        descs = list(ex.map(lambda im: _vision(u, *im), imgs))
    parts = []
    if kind == "video":
        parts += [f"Кадр {i + 1}" + (f" ({tl[i]} с)" if i < len(tl) else "") + f": {d}" for i, d in enumerate(descs) if d]
    elif descs and descs[0]:
        parts.append(descs[0])
    feats, speech = None, ""
    if audio:
        adata = await audio.read()
        if len(adata) > 20 * 1024 * 1024:
            raise HTTPException(413, "audio too large")
        try:
            feats = umedia.features(adata, c.vehicle.engine_model)
        except (ValueError, EOFError, Exception) as e:  # noqa: BLE001 - a broken track must not kill the photo part
            feats = {"quality": "bad", "quality_ru": "не удалось прочитать", "notes": [f"Звук не прочитан: {type(e).__name__}"]}
        if kind == "sound":
            name = f"{stem}.wav"; (up / name).write_bytes(adata); files.append(name)
        if kind == "video" and feats.get("duration_s"):
            try:  # speech in the clip ("вот тут стучит") goes to the agent; engine noise alone gives nothing
                txt, _eng = voice.transcribe(adata, "audio/wav", LANGS[lang_or_default(u.get("lang"))]["stt"], S,
                                             local_first=privacy_laws.requires_localization(u.get("country")), country=u.get("country"))
                speech = umedia.meaningful_speech(txt)
            except voice.SttUnavailable:
                speech = ""
        if feats.get("duration_s") or kind == "sound":
            parts.append(umedia.describe_sound(feats).replace("Запись звука двигателя с телефона", "Звук ролика" if kind == "video" else "Запись звука двигателя с телефона"))
        if speech:
            parts.append(f"Механик говорит в ролике: «{pii.mask(speech)}»")
    note = pii.mask("\n".join(parts)) if parts else ""
    if kind != "sound" and not any(descs):
        note = (note + "\n" if note else "") + "Распознать изображение не удалось (сервис зрения недоступен)."
    mid = STORE.add_media(u["id"], vehicle_id, kind, files, note[:3000], feats, pii.mask(text)[:500])
    prof = uprofile.load(u); level = u.get("experience") or "experienced"
    turn = AGENT.handle(c, {**u, "_vocab_hint": uprofile.prompt_hint(prof, level)}, text or MEDIA_ASK[kind],
                        image_note=f"{ {'photo': 'фото', 'video': 'видео', 'sound': 'звук'}[kind] }: {note}")
    turn.text, _ = uprofile.gloss(turn.text, prof, level)
    label = {"photo": "[фото]", "video": "[видео]", "sound": "[звук двигателя]"}[kind]
    _record(u, c, f"media_{kind}", ((text + " ") if text else "") + label, turn, t0,
            {"photo": files[0] if kind != "sound" else None, "photo_desc": note[:1000], "media_id": mid, "media_kind": kind})
    plate = find_plate(" ".join(descs)) if any(descs) else None
    return {"media_id": mid, "kind": kind, "files": files, "description": note, "features": feats, "speech": speech,
            "plate": plate, "turn": turn.as_dict()}


class TranslateIn(BaseModel):
    texts: list[str]
    lang: str | None = None
    refs: list[dict | None] | None = None


@app.post("/api/translate")
def translate_texts(body: TranslateIn, uid: str | None = Cookie(None)) -> dict:
    """Russian content (manual pages, tickets, chips, history) in the interface language until people translate the
    databases: imported translation -> cache -> LLM. A result that lost a number keeps the Russian original."""
    u = user_of(uid, manager=False)
    lang = body.lang if body.lang in LANGS else lang_or_default(u.get("lang"))
    texts = [t[:6000] for t in body.texts[:80]]
    if sum(map(len, texts)) > 60000:
        raise HTTPException(413, "too much text")
    res = TR.translate(texts, lang, body.refs)
    return {"lang": lang, "items": res}


@app.get("/api/translate/stats")
def translate_stats(uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    return TR.stats()


@app.post("/api/stt")
async def stt(audio: UploadFile = File(...), lang: str = Form("ru"), uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    data = await audio.read()
    try:
        # data-localization countries (RU, UZ, KZ): local Whisper first, cloud only if the local one is unavailable
        text, engine = voice.transcribe(data, audio.content_type or "audio/webm", LANGS[lang_or_default(lang)]["stt"], S,
                                        local_first=privacy_laws.requires_localization(u.get("country")), country=u.get("country"))
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
    token: str | None = None  # the signed draft from the card (see draft_token)


@app.post("/api/ticket/confirm")
def confirm(body: ConfirmIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    t0 = time.time()
    c = AGENT.conv(u["id"], body.vehicle_id)
    if c.draft is None or (body.draft_id and c.draft.get("id") != body.draft_id):
        d = draft_from_token(u["id"], body.vehicle_id, body.token)
        if d and (not body.draft_id or d.get("id") == body.draft_id):
            with c.lock:
                c.draft = d
                AGENT.restore_check(c, (d.get("request") or {}).get("session_id"))
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


_FOOTER_RE = re.compile(r"\n[ \t]*(\d{1,4}|Страница \d{1,4}|- ?\d{1,4} ?-)[ \t]*\s*$")


def view_text(text: str) -> str:
    """Page text for the viewer without the printed page number (or «Страница N») on its last line."""
    return _FOOTER_RE.sub("", text or "").rstrip()


@app.get("/api/page/{doc_id}/{page}")
def page_json(doc_id: str, page: int, uid: str | None = Cookie(None)) -> dict:
    user_of(uid)
    p, extra = _page(doc_id, page)
    meta = AGENT.docs.get(doc_id, {})
    return {"doc_id": doc_id, "document": p.document, "full_title": meta.get("full_title", p.document), "page": page, "text": view_text(p.text), "extra": extra,
            "has_image": bool((not extra and (Path(S.manuals_dir) / meta.get("filename", "_")).exists() and shutil.which("pdftoppm")) or (extra and p.image))}


@app.get("/api/section/{doc_id}/{page}")
def section_json(doc_id: str, page: int, q: str = "", uid: str | None = Cookie(None)) -> dict:
    """Full text of the section/procedure containing the cited line, with its page span."""
    user_of(uid)
    p, extra = _page(doc_id, page)
    sec = AGENT.sections.find(doc_id, page, q)
    meta = AGENT.docs.get(doc_id, {})
    pdf = not extra and (Path(S.manuals_dir) / meta.get("filename", "_")).exists()
    pdf_text = not pdf  # backup stand: /manuals/<doc>.pdf?page=N is a printable text page of the section (no PDF published there)
    imgs = bool((pdf and shutil.which("pdftoppm")) or (extra and p.image and (Path(S.extra_images_dir) / p.image).exists()))
    d = sec.as_dict() if sec else {"pages": [{"page": page, "text": p.text}], "start_page": page, "end_page": page, "span": str(page), "title": "", "label": ""}
    d["pages"] = [{**x, "text": view_text(x.get("text", ""))} for x in d.get("pages", [])]
    return {"document": p.document, "full_title": meta.get("full_title", p.document), "extra": extra, "pdf": pdf, "pdf_text": pdf_text, "has_image": imgs, **d}


class ExtractIn(BaseModel):
    doc_id: str
    page: int
    q: str = ""
    question: str = ""


@app.post("/api/extract")
def extract(body: ExtractIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    _page(body.doc_id, body.page)
    return AGENT.section_extract(body.doc_id, body.page, body.q, pii.mask(body.question)[:300], LANGS[answer_lang(u)]["llm"])


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


PRINT_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{title}}</title><link rel="stylesheet" href="/static/app.css"><style>
body{overflow:auto}main{max-width:760px;margin:0 auto;padding:20px 16px 48px}h1{font-size:1.25rem;margin:0 0 4px}.src{color:var(--muted);margin:0 0 12px}
.note{background:var(--surface-2);border:1px solid var(--border);border-radius:12px;padding:10px 12px;font-size:.9rem;margin:0 0 16px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}.bar a,.bar button{min-height:44px;padding:0 16px;border-radius:12px;border:1px solid var(--border);
background:var(--surface-2);color:var(--text);font:inherit;display:inline-flex;align-items:center;text-decoration:none;cursor:pointer}.bar .pr{background:var(--accent);color:#fff;border:0}
h2{font-size:1rem;margin:20px 0 6px;color:var(--muted)}h2.on{color:var(--text)}pre{white-space:pre-wrap;word-break:break-word;font:inherit;line-height:1.5;margin:0}
h3{font-size:.95rem;margin:14px 0 4px}.tr{margin:0 0 8px;padding-left:10px;border-left:3px solid var(--border);line-height:1.45}
@media print{.bar,.note{display:none}body{background:#fff;color:#000}h2{color:#000}main{max-width:none;padding:0}}</style></head>
<body><main>{{body}}</main><script src="/static/print.js"></script></body></html>"""


def _reflow(t: str) -> str:
    """PDF text layer has hard line breaks: join a line with the next when the sentence clearly continues (same rule as app.js)."""
    t = re.sub(r"([а-яё])-\n(?=[а-яё])", r"\1", t)
    return re.sub(r"([^\n.:;!?])\n(?=[a-zа-яё(«])", r"\1 ", t)


def _print_body(t: str) -> str:
    """Fault tables row by row (cause in bold — remedy, symptom headings), other pages as reflowed text."""
    from ..agent.core import _TABLE_HEAD_RE
    if not _TABLE_HEAD_RE.search(t):
        return f"<pre>{html.escape(_reflow(t))}</pre>"
    out = []
    for line in t.split("\n"):
        line = line.strip()
        if not line:
            continue
        if " — " in line:
            a, b = line.split(" — ", 1)
            out.append(f"<p class='tr'><b>{html.escape(a)}</b> — {html.escape(b)}</p>")
        else:
            out.append(f"<h3>{html.escape(line)}</h3>")
    return "".join(out)


@app.get("/manuals/{doc_id}.pdf")
def manual_pdf(doc_id: str, page: int = 0, uid: str | None = Cookie(None)):
    user_of(uid)
    meta = AGENT.docs.get(doc_id)
    if meta and (Path(S.manuals_dir) / meta["filename"]).exists():
        # inline: open in the browser's viewer, never as a download (mobile used to offer «Скачать»)
        return FileResponse(Path(S.manuals_dir) / meta["filename"], media_type="application/pdf", filename=f"{doc_id}.pdf",
                            content_disposition_type="inline", headers={"Cache-Control": "private, max-age=3600"})
    # backup stand (Vercel): the manual PDFs are not published there (no redistribution right), only the indexed page text
    # the app already shows -> a printable page of the cited section («Сохранить в PDF» = the browser's print dialog), never a bare 404
    page = page if page > 0 else 1  # a bare /manuals/<doc>.pdf (no ?page=, e.g. an old link): the start of the manual
    try:
        p, extra = _page(doc_id, page)
    except HTTPException:
        return HTMLResponse(PRINT_HTML.replace("{{title}}", "Страница не найдена").replace("{{body}}",
                            "<h1>Страница не найдена</h1><p class='note'>Такой страницы нет в руководствах этого стенда.</p><div class='bar'><a href='/'>← В приложение</a></div>"), status_code=404)
    sec = AGENT.sections.find(doc_id, page, "")
    pages = (sec.as_dict().get("pages") if sec else None) or [{"page": page, "text": p.text}]
    title = (meta or {}).get("full_title", p.document)
    e = html.escape
    body = (f"<h1>{e(title)}</h1><p class='src'>с. {e(str(pages[0]['page']))}" + (f"–{e(str(pages[-1]['page']))}" if len(pages) > 1 else "") + "</p>"
            "<div class='bar'><button class='pr' id='pr'>Сохранить в PDF / печать</button><a href='/'>← В приложение</a></div>"
            "<p class='note'>Оригинальный PDF открывается на основном стенде. Здесь — текст этих страниц из руководства (тот же, что в приложении); "
            "кнопка выше сохраняет его в PDF.</p>"
            + "".join(f"<h2 id='p{x['page']}' class='{'on' if x['page'] == page else ''}'>Страница {x['page']}</h2>{_print_body(view_text(x.get('text', '')))}" for x in pages))
    return HTMLResponse(PRINT_HTML.replace("{{title}}", e(f"{title}, с. {page}")).replace("{{body}}", body), headers={"Cache-Control": "private, max-age=600"})


# ---------------------------------------------------------------- audit / ops
def _staff_ids(company_id: str | None) -> set[str]:
    """Accounts of one company (None: everybody)."""
    return {u["id"] for u in STORE.q("SELECT id, role, company_id FROM users") if company_id is None or tenancy.company_of(u) == company_id}


@app.get("/api/audit")
def audit(limit: int = 200, uid: str | None = Cookie(None)) -> dict:
    me_ = user_of(uid, manager=True)
    staff = _staff_ids(tenancy.company_of(me_))
    rows = [r for r in STORE.q("SELECT a.*, u.name AS user_name FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 5000")
            if r["user_id"] in staff][:min(limit, 2000)]
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
    me_ = user_of(uid, manager=True)
    staff = _staff_ids(tenancy.company_of(me_))
    rows = [{k: v for k, v in r.items() if k != "user_id"} for r in STORE.q("SELECT id, datetime(ts,'unixepoch','localtime') AS time, user_id, substr(user_id,1,8) AS user, role, vehicle_id, action, input, output, sources, latency_ms, tokens, ok FROM audit ORDER BY id DESC LIMIT 5000") if r["user_id"] in staff]
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit.csv"})


@app.get("/api/health")
def health() -> dict:
    # public: no model name or other configuration details (they are in the engineer menu, behind the engineer PIN)
    return {"ml": AGENT.ml.health(), "toir": AGENT.toir.health(), "llm_configured": AGENT.llm.available,
            "pages": len(AGENT.index.pages), "extra_pages": len(AGENT.extra.pages) if AGENT.extra else 0,
            "stt": "server" if (S.speech_url or S.deepgram_api_key or S.cloudru_api_key) else "browser",
            "tts": "server" if (S.speech_url or S.silero_enabled) else "browser"}


@app.get("/api/usage")
def usage(uid: str | None = Cookie(None)) -> dict:
    me_ = user_of(uid, manager=True)
    return company_usage(tenancy.company_of(me_))


def company_usage(company_id: str | None) -> dict:
    """Per-company LLM use from the audit log (each request records its tokens). Cost = tokens × LLM_PRICE_RUB_PER_M
    (0 for the free models used now)."""
    staff = _staff_ids(company_id)
    rows = [r for r in STORE.q("SELECT user_id, tokens, latency_ms FROM audit WHERE tokens > 0") if r["user_id"] in staff]
    tok = sum(int(r["tokens"] or 0) for r in rows)
    price = float(os.environ.get("LLM_PRICE_RUB_PER_M", "0") or 0)
    return {"calls": len(rows), "prompt_tokens": tok, "completion_tokens": 0, "tokens": tok,
            "avg_seconds": round(sum((r["latency_ms"] or 0) for r in rows) / len(rows) / 1000, 1) if rows else 0,
            "cost_rub": round(tok * price / 1e6, 2)}


# ---------------------------------------------------------------- engineer menu (role "engineer" only)
STORE.x("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT, ts REAL, by TEXT)")
STORE.x("CREATE TABLE IF NOT EXISTS mcp_servers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, transport TEXT, token_env TEXT, "
        "enabled INTEGER DEFAULT 1, status TEXT, tools TEXT, checked_at REAL, created_at REAL, by TEXT)")
# runtime knobs the engineer may change; secrets (keys) are never here: they stay in env / Vercel env
MODEL_KNOBS = {"llm_model": str, "llm_vision_model": str, "llm_timeout_s": float, "llm_max_tool_rounds": int,
               "stt_order": str, "deepgram_model": str}
STT_ORDERS = ("local-first", "cloud-first")
for _r in STORE.q("SELECT k, v FROM kv WHERE k LIKE 'cfg.%'"):
    _k = _r["k"][4:]
    if _k in MODEL_KNOBS:
        try:
            setattr(S, _k, MODEL_KNOBS[_k](_r["v"]))
        except (ValueError, TypeError):
            pass


def _models_view() -> dict:
    opts = [m.strip() for m in os.environ.get("LLM_MODELS", "").split(",") if m.strip()]
    cur = {k: getattr(S, k, None) for k in MODEL_KNOBS}
    return {"current": cur, "llm_options": sorted(set(opts + [S.llm_model])), "stt_orders": STT_ORDERS,
            "stt_engines": {"cloudru": bool(S.cloudru_api_key and S.cloudru_stt), "whisper_local": bool(S.speech_url), "deepgram": bool(S.deepgram_api_key), "browser": True},
            "deepgram_langs": sorted(voice.DEEPGRAM_LANGS), "no_whisper": sorted(voice.NO_WHISPER), "llm_configured": AGENT.llm.available}


@app.get("/engineer", response_class=HTMLResponse)
def engineer_page() -> str:
    return (STATIC / "engineer.html").read_text(encoding="utf-8")


@app.get("/api/eng/logs")
def eng_logs(after: int = 0, limit: int = 100, action: str | None = None, company: str | None = None, uid: str | None = Cookie(None)) -> dict:
    """Live log / query history: audit rows newer than `after` (the page polls)."""
    user_of(uid, roles=("engineer",))
    q = "SELECT a.id, a.ts, a.role, a.vehicle_id, a.action, a.input, a.output, a.tools, a.latency_ms, a.tokens, a.ok, a.user_id, u.name AS user_name, u.company_id AS company_id FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.id>?"
    args: list = [after]
    if action:
        q += " AND a.action=?"; args.append(action)
    rows = STORE.q(q + " ORDER BY a.id DESC LIMIT ?", (*args, 5000 if company else min(limit, 500)))
    for r in rows:
        r["company_id"] = None if r["role"] == "engineer" else (r["company_id"] or tenancy.SEED_ID)
        r.pop("user_id", None)
    if company:
        rows = [r for r in rows if r["company_id"] == company][:min(limit, 500)]
    for r in rows:
        r["input"], r["output"] = (r["input"] or "")[:200], (r["output"] or "")[:240]
        try:
            r["tools"] = [t.get("tool") for t in json.loads(r["tools"] or "[]")]
        except (json.JSONDecodeError, AttributeError):
            r["tools"] = []
    return {"rows": rows, "last": max([r["id"] for r in rows] + [after])}


@app.get("/api/eng/trace/{aid}")
def eng_trace(aid: int, uid: str | None = Cookie(None)) -> dict:
    """Debug view of one request: input, answer, every tool call with its result flags, sources, tokens, latency."""
    user_of(uid, roles=("engineer",))
    r = STORE.one("SELECT a.*, u.name AS user_name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.id=?", (aid,))
    if not r:
        raise HTTPException(404, "no such request")
    for k in ("tools", "sources"):
        try:
            r[k] = json.loads(r[k]) if r[k] else []
        except json.JSONDecodeError:
            pass
    r["user_id"] = (r["user_id"] or "")[:8]
    return r


@app.get("/api/eng/models")
def eng_models(uid: str | None = Cookie(None)) -> dict:
    user_of(uid, roles=("engineer",))
    return _models_view()


class KnobsIn(BaseModel):
    values: dict


@app.post("/api/eng/models")
def eng_models_set(body: KnobsIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid, roles=("engineer",))
    changed = {}
    for k, v in body.values.items():
        if k not in MODEL_KNOBS or v in (None, ""):
            continue
        if k == "stt_order" and v not in STT_ORDERS:
            raise HTTPException(400, "stt_order: local-first | cloud-first")
        try:
            val = MODEL_KNOBS[k](v)
        except (ValueError, TypeError):
            raise HTTPException(400, f"bad value for {k}")
        if k == "llm_timeout_s" and not 5 <= val <= 300 or k == "llm_max_tool_rounds" and not 1 <= val <= 10:
            raise HTTPException(400, f"{k} out of range")
        setattr(S, k, val)
        STORE.x("INSERT OR REPLACE INTO kv(k, v, ts, by) VALUES (?,?,?,?)", (f"cfg.{k}", str(val), time.time(), u["id"]))
        changed[k] = val
    if "llm_max_tool_rounds" in changed and hasattr(AGENT, "max_rounds"):
        AGENT.max_rounds = changed["llm_max_tool_rounds"]
    STORE.audit(user_id=u["id"], role=u["role"], action="eng_models", input=json.dumps(changed, ensure_ascii=False), ok=1)
    return _models_view()


@app.get("/api/eng/health")
def eng_health(uid: str | None = Cookie(None)) -> dict:
    """ML / TOiR stubs and the other services, each with its response time."""
    user_of(uid, roles=("engineer",))
    out = {}
    for name, fn in (("ml", AGENT.ml.health), ("toir", AGENT.toir.health)):
        t0 = time.time()
        try:
            ok = bool(fn())
        except Exception:  # noqa: BLE001
            ok = False
        out[name] = {"ok": ok, "ms": int((time.time() - t0) * 1000)}
    out["llm"] = {"ok": AGENT.llm.available, "model": S.llm_model, "providers": AGENT.llm.order("RU"), "providers_other": AGENT.llm.order("IN"),
                  "cloudru_model": S.cloudru_model if S.cloudru_api_key else None, "usage": AGENT.llm.usage.as_dict()}
    out["stt"] = {"ok": True, "engines": _models_view()["stt_engines"], "order": S.stt_order}
    out["index"] = {"ok": True, "pages": len(AGENT.index.pages), "extra_pages": len(AGENT.extra.pages) if AGENT.extra else 0}
    out["translate"] = {"ok": True, **TR.stats()}
    errs = STORE.q("SELECT action, COUNT(*) AS n FROM audit WHERE ok=0 AND ts>? GROUP BY action ORDER BY n DESC LIMIT 8", (time.time() - 86400,))
    return {"services": out, "errors_24h": errs, "shared_state": {"on": REPLICA.on, **REPLICA.stats}}


class McpIn(BaseModel):
    name: str
    url: str
    transport: str = "http"
    token_env: str = ""
    enabled: bool = True


def _mcp_probe(url: str, token_env: str) -> tuple[str, list[str]]:
    """Streamable-HTTP MCP: initialize + tools/list. The token is read from an env variable named by the engineer
    (keys are never stored in the DB)."""
    import httpx

    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token_env and os.environ.get(token_env):
        h["Authorization"] = f"Bearer {os.environ[token_env]}"
    def rpc(cl, method, params, sid=None, i=1):
        hh = dict(h, **({"Mcp-Session-Id": sid} if sid else {}))
        r = cl.post(url, json={"jsonrpc": "2.0", "id": i, "method": method, "params": params}, headers=hh)
        txt = r.text
        body = json.loads(txt.split("data:", 1)[1].split("\n", 1)[0]) if txt.lstrip().startswith(("event:", "data:")) else r.json()
        return r, body
    try:
        with httpx.Client(timeout=8, follow_redirects=False) as cl:
            r, b = rpc(cl, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "digital-mechanic", "version": "1"}})
            if r.status_code >= 400 or "error" in b:
                return f"error {r.status_code}", []
            sid = r.headers.get("mcp-session-id")
            cl.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=dict(h, **({"Mcp-Session-Id": sid} if sid else {})))
            _, t = rpc(cl, "tools/list", {}, sid, 2)
            return "ok", [x.get("name", "") for x in (t.get("result") or {}).get("tools", [])][:50]
    except Exception as e:  # noqa: BLE001
        return f"unreachable ({type(e).__name__})", []


@app.get("/api/eng/mcp")
def eng_mcp(uid: str | None = Cookie(None)) -> dict:
    user_of(uid, roles=("engineer",))
    rows = STORE.q("SELECT * FROM mcp_servers ORDER BY id")
    for r in rows:
        r["tools"] = json.loads(r["tools"] or "[]")
    return {"servers": rows}


@app.post("/api/eng/mcp")
def eng_mcp_add(body: McpIn, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid, roles=("engineer",))
    if not re.match(r"^https?://[^\s]+$", body.url) or body.transport not in ("http", "sse"):
        raise HTTPException(400, "url must be http(s); transport http | sse")
    if body.token_env and not re.match(r"^[A-Z][A-Z0-9_]{1,63}$", body.token_env):
        raise HTTPException(400, "token_env: name of an environment variable (the key itself is never stored)")
    STORE.x("INSERT INTO mcp_servers(name,url,transport,token_env,enabled,created_at,by) VALUES (?,?,?,?,?,?,?)",
            (body.name[:60], body.url[:300], body.transport, body.token_env, int(body.enabled), time.time(), u["id"]))
    STORE.audit(user_id=u["id"], role=u["role"], action="eng_mcp_add", input=f"{body.name} {body.url}", ok=1)
    return eng_mcp(uid)


@app.post("/api/eng/mcp/{sid}/check")
def eng_mcp_check(sid: int, uid: str | None = Cookie(None)) -> dict:
    user_of(uid, roles=("engineer",))
    r = STORE.one("SELECT * FROM mcp_servers WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "no such server")
    st, tools = _mcp_probe(r["url"], r["token_env"] or "")
    STORE.x("UPDATE mcp_servers SET status=?, tools=?, checked_at=? WHERE id=?", (st, json.dumps(tools), time.time(), sid))
    return {"status": st, "tools": tools}


class McpPatch(BaseModel):
    enabled: bool | None = None
    delete: bool = False


@app.post("/api/eng/mcp/{sid}")
def eng_mcp_edit(sid: int, body: McpPatch, uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid, roles=("engineer",))
    if body.delete:
        STORE.x("DELETE FROM mcp_servers WHERE id=?", (sid,))
    elif body.enabled is not None:
        STORE.x("UPDATE mcp_servers SET enabled=? WHERE id=?", (int(body.enabled), sid))
    STORE.audit(user_id=u["id"], role=u["role"], action="eng_mcp_edit", input=f"{sid} {body.model_dump()}", ok=1)
    return eng_mcp(uid)


# ---------------------------------------------------------------- users (the chief manages employees)
class NewUserIn(BaseModel):
    name: str
    email: str = ""
    role: str = "mechanic"
    experience: str = "experienced"
    title: str = ""
    vehicles: list[str] = []
    lang: str = "ru"
    country: str = "RU"


class UserPatch(BaseModel):
    email: str | None = None
    active: bool | None = None
    vehicles: list[str] | None = None
    experience: str | None = None
    title: str | None = None
    role: str | None = None
    new_pin: bool = False


def _demo_uids() -> set[str]:
    return {a["uid"] for a in DEMO["accounts"]}


def chief_of(uid: str | None) -> dict:
    """Employees are managed only by a chief who signed in with a code + PIN (demo «chief» or an account the chief
    created). A name-only «Главный механик» session from the open login screen sees the fleet but not the staff."""
    u = user_of(uid, manager=True)
    if not (u.get("login") or u["id"] in _demo_uids()):
        raise HTTPException(403, "chief account only (code + PIN)")
    return u


def _new_pin() -> str:
    import secrets
    return f"{secrets.randbelow(9000) + 1000}"


def _user_row(u: dict) -> dict:
    p = uprofile.load(u)
    return {"id": u["id"], "login": u.get("login"), "name": u["name"], "role": u["role"], "title": u.get("title"), "experience": u.get("experience"), "email": u.get("email"),
            "lang": u.get("lang"), "country": u.get("country"), "active": bool(u.get("active", 1)), "onboarded": bool(u.get("onboarded", 1)),
            "vehicles": json.loads(u.get("vehicles") or "[]"), "style": ustyle.public(p, u)["text"], "style_v": ustyle.public(p, u)["v"],
            "persona": ustyle.public(p, u)["persona"], "created_by": (u.get("created_by") or "")[:12]}


@app.get("/api/admin/users")
def admin_users(uid: str | None = Cookie(None)) -> dict:
    me_ = chief_of(uid)
    co = tenancy.company_of(me_)
    rows = [u for u in STORE.q("SELECT * FROM users WHERE login IS NOT NULL ORDER BY role, name") if tenancy.company_of(u) == co and u["role"] != "engineer"]
    return {"users": [_user_row(u) for u in rows], "roles": ["mechanic", "manager"], "levels": list(EXPERIENCE_LEVELS),
            "vehicles": AGENT.fleet.ids()}


@app.post("/api/admin/users")
def admin_create_user(body: NewUserIn, uid: str | None = Cookie(None)) -> dict:
    """Only the chief creates accounts. The PIN is shown once in the answer (stored as a salted hash)."""
    me_ = chief_of(uid)
    if body.role not in ("mechanic", "manager") or body.experience not in EXPERIENCE_LEVELS:  # engineers are not company staff
        raise HTTPException(400, "bad role or level")
    bad = [v for v in body.vehicles if v not in AGENT.fleet.ids()]
    if bad:
        raise HTTPException(400, f"unknown vehicles: {', '.join(bad)}")
    name = pii.mask(body.name.strip()[:60]) or "—"
    new = "u" + uuid.uuid4().hex[:10]
    login = f"{re.sub(r'[^a-z0-9]', '', name.lower().translate(str.maketrans('абвгдеёжзийклмнопрстуфхцчшщъыьэюя', 'abvgdeejziiklmnoprstufhccss_y_eua'))) or 'user'}{secrets_suffix()}"
    pin = _new_pin()
    STORE.upsert_user(new, name=name, role=body.role, experience=body.experience, title=body.title[:80], vehicles=json.dumps(body.vehicles),
                      lang=lang_or_default(body.lang), country=privacy_laws.country(body.country), login=login, pin_hash=_pin_hash(pin), active=1,
                      onboarded=0, created_by=me_["id"], company_id=tenancy.company_of(me_), email=_email_or_none(body.email))
    STORE.audit(user_id=me_["id"], role=me_["role"], action="admin_user_create", input=f"{login} {body.role}", ok=1)
    return {"user": _user_row(STORE.user(new)), "login": login, "pin": pin, "invite": f"/?login={login}"}


def secrets_suffix() -> str:
    import secrets
    return str(secrets.randbelow(90) + 10)


@app.post("/api/admin/users/{user_id}")
def admin_edit_user(user_id: str, body: UserPatch, uid: str | None = Cookie(None)) -> dict:
    me_ = chief_of(uid)
    u = STORE.user(user_id)
    if not u or u["role"] == "engineer" or tenancy.company_of(u) != tenancy.company_of(me_):
        raise HTTPException(404, "no such user")
    if user_id == me_["id"] and body.active is False:
        raise HTTPException(400, "cannot deactivate yourself")
    if user_id in _demo_uids() and (body.new_pin or body.active is False or (body.role and body.role != u["role"])):
        raise HTTPException(400, "demo account: PIN, role and status are fixed")  # the login screen shows its PIN to everyone
    kw: dict = {}
    if body.active is not None:
        kw["active"] = int(body.active)
    if body.vehicles is not None:
        bad = [v for v in body.vehicles if v not in AGENT.fleet.ids()]
        if bad:
            raise HTTPException(400, f"unknown vehicles: {', '.join(bad)}")
        kw["vehicles"] = json.dumps(body.vehicles)
    if body.email is not None:
        kw["email"] = _email_or_none(body.email)
    if body.experience in EXPERIENCE_LEVELS:
        kw["experience"] = body.experience
    if body.title is not None:
        kw["title"] = body.title[:80]
    if body.role in ("mechanic", "manager"):
        kw["role"] = body.role
    pin = None
    if body.new_pin:
        pin = _new_pin()
        kw["pin_hash"] = _pin_hash(pin)
    STORE.upsert_user(user_id, **kw)
    STORE.audit(user_id=me_["id"], role=me_["role"], action="admin_user_edit", input=f"{user_id} {sorted(kw)}", ok=1)
    return {"user": _user_row(STORE.user(user_id)), **({"pin": pin} if pin else {})}


@app.get("/api/admin/users/{user_id}")
def admin_user_card(user_id: str, uid: str | None = Cookie(None)) -> dict:
    """Profile card by user ID: style, interests and typical topics, trucks worked on, history."""
    me_ = chief_of(uid)
    u = STORE.user(user_id)
    if not u or u["role"] == "engineer" or tenancy.company_of(u) != tenancy.company_of(me_):
        raise HTTPException(404, "no such user")
    p = uprofile.load(u)
    acc = next((a for a in DEMO["accounts"] if a["uid"] == user_id), {})
    msgs = STORE.q("SELECT ts, vehicle_id, content FROM messages WHERE user_id=? AND role='user' ORDER BY ts DESC LIMIT 40", (user_id,))
    from collections import Counter
    topics = Counter()
    for m in msgs:
        for k in uprofile.terms_in(m["content"]):
            topics[k] += 1
    trucks = Counter(m["vehicle_id"] for m in msgs if m["vehicle_id"])
    scans = STORE.q("SELECT vehicle_id, COUNT(*) AS n, MAX(ts) AS last FROM diagnoses WHERE user_id=? GROUP BY vehicle_id ORDER BY last DESC", (user_id,))
    hist = [dict(e, vehicle_id=vid) for vid, evs in AGENT.demo.items() for e in evs if e.get("mechanic") == u["name"]]
    hist += [{"vehicle_id": r["vehicle_id"], "date": time.strftime("%Y-%m-%d", time.localtime(r["started"])), "ticket_id": r["ticket_id"],
              "repair_steps": [x.get("text") for x in json.loads(r["steps"] or "[]")], "title": r["title"]}
             for r in STORE.q("SELECT * FROM repairs WHERE user_id=? ORDER BY started DESC LIMIT 20", (user_id,))]
    assigned = [h for h in STORE.handoffs(None) if h.get("kind") == "assign" and (h.get("payload") or {}).get("assignee_uid") == user_id]
    return {"user": _user_row(u), "style": ustyle.public(p, u), "interests": acc.get("interests") or [],
            "topics": [k for k, _ in topics.most_common(8)] or sorted(p.get("terms", {}), key=lambda k: -p["terms"][k])[:8],
            "note": acc.get("note"), "vehicles": json.loads(u.get("vehicles") or "[]"),
            "worked_on": sorted(set([s_["vehicle_id"] for s_ in scans] + list(trucks) + [h["vehicle_id"] for h in hist])),
            "scans": scans[:12], "history": sorted(hist, key=lambda e: e.get("date") or "", reverse=True)[:12],
            "assigned": [{"ticket_id": h["payload"].get("ticket_id"), "vehicle_id": h["vehicle_id"], "status": h["status"]} for h in assigned],
            "recent_questions": [{"ts": m["ts"], "vehicle_id": m["vehicle_id"], "text": m["content"][:160]} for m in msgs[:6]]}


# ---------------------------------------------------------------- onboarding (first login of a new employee)
@app.get("/api/onboarding")
def onboarding_sample(uid: str | None = Cookie(None)) -> dict:
    u = user_of(uid)
    return {"needed": not u.get("onboarded", 1), "sample": ustyle.ONBOARD_SAMPLE, "levels": list(EXPERIENCE_LEVELS), "details": list(DETAIL_LEVELS),
            "experience": u.get("experience"), "detail": u.get("detail") or "brief"}


class OnboardIn(BaseModel):
    formality: str
    detail: str = "brief"
    experience: str = "experienced"


@app.post("/api/onboarding")
def onboarding_done(body: OnboardIn, uid: str | None = Cookie(None)) -> dict:
    """The choice seeds the style card (v1); it then evolves after sessions and stays editable in settings."""
    u = user_of(uid)
    if body.formality not in ustyle.FORMALITY or body.detail not in DETAIL_LEVELS or body.experience not in EXPERIENCE_LEVELS:
        raise HTTPException(400, "bad choice")
    u = STORE.upsert_user(u["id"], detail=body.detail, experience=body.experience, onboarded=1)
    p = uprofile.load(u)
    p["formality"] = body.formality
    p.pop("style", None)
    st = ustyle.card(p, u)
    st["source"] = "onboarding"
    STORE.upsert_user(u["id"], profile=uprofile.dump(p))
    STORE.audit(user_id=u["id"], role=u["role"], action="onboarding", input=f"{body.formality} {body.detail} {body.experience}", ok=1)
    return {"user": pub(STORE.user(u["id"])), "style": ustyle.public(p, u)}


@app.get("/api/my/assignments")
def my_assignments(uid: str | None = Cookie(None)) -> dict:
    """Mechanic: tickets the chief handed to him (manager briefing -> «назначить»)."""
    u = user_of(uid, roles=("mechanic",))
    out = []
    for h in STORE.handoffs(None):
        p = h.get("payload") or {}
        p = json.loads(p) if isinstance(p, str) else p
        if h.get("kind") == "assign" and p.get("assignee_uid") == u["id"] and h.get("status") != "closed":
            out.append({"id": h["id"], "vehicle_id": h["vehicle_id"], "ticket_id": p.get("ticket_id"), "summary": h.get("summary"), "ts": h["ts"]})
    return {"assignments": out}


# ---------------------------------------------------------------- companies (tenants): engineer only
def _email_or_none(e: str | None) -> str | None:
    e = (e or "").strip()[:120]
    return e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e) else None


LLM_PRIMARY_OPTS = ("", "auto", "tokenrouter", "cloudru")


class CompanyIn(BaseModel):
    name: str
    llm_primary: str = ""
    note: str = ""


class CompanyPatch(BaseModel):
    name: str | None = None
    llm_primary: str | None = None
    active: bool | None = None
    note: str | None = None
    vehicles: list[str] | None = None  # trucks of this company (moved from wherever they were)
    delete: bool = False  # remove the company with its people; its trucks go back to the seed company


class EngUserPatch(BaseModel):
    active: bool | None = None
    delete: bool = False


class ChiefIn(BaseModel):
    name: str
    pin: str = ""
    style: str = ""  # how the assistant talks to him («на вы, коротко, цифры с единицами»)
    formality: str = ""  # "ty" | "vy"
    email: str = ""
    title: str = "Главный механик"


def _company_view(c: dict) -> dict:
    vc = tenancy.vehicle_company(STORE, AGENT.fleet.all_ids())
    staff = [u for u in STORE.q("SELECT id, name, role, login, company_id, active FROM users") if u["role"] != "engineer" and tenancy.company_of(u) == c["id"]]
    return {**c, "vehicles": sorted(v for v, k in vc.items() if k == c["id"]), "chiefs": [{"id": u["id"], "name": u["name"], "login": u["login"]} for u in staff if u["role"] == "manager"],
            "staff": len(staff), "usage": company_usage(c["id"])}


@app.get("/api/eng/companies")
def eng_companies(uid: str | None = Cookie(None)) -> dict:
    user_of(uid, roles=("engineer",))
    return {"companies": [_company_view(c) for c in STORE.q("SELECT * FROM companies ORDER BY created")], "llm_primary": list(LLM_PRIMARY_OPTS),
            "all_vehicles": AGENT.fleet.all_ids()}


class CleanupIn(BaseModel):
    """Audit leftovers: everything one account wrote since the audit started (tickets, handoffs), optionally one vehicle."""
    since: float | None = None
    user_id: str = ""
    vehicle_id: str = ""
    keep_ticket_ids: list[str] = []
    keep_handoff_ids: list[int] = []


@app.post("/api/eng/cleanup-test-data")
def eng_cleanup_test_data(body: CleanupIn | None = None, uid: str | None = Cookie(None)) -> dict:
    """Load-test leftovers off the chief's dashboard (Vercel 26.11: 40 «race-222019-3» handoffs from handoff_race.py and
    TKT-0001/TKT-0002 three times each — the embedded TOiR mock restarted numbering before 8d2a2cd). Deletes handoffs whose
    note is a race marker and, for a ticket number seen more than once, every copy but the newest. Engineer only; the
    deletions reach the other instances through the shared copy (replica merge deletes by row identity)."""
    me_ = user_of(uid, roles=("engineer",))
    race = [r["id"] for r in STORE.q("SELECT id, note FROM handoffs") if re.fullmatch(r"race-\d{6}-\d+", (r["note"] or "").strip())]
    for hid in race:
        STORE.x("DELETE FROM handoffs WHERE id=?", (hid,))
    dup = STORE.q("SELECT t.id FROM tickets t WHERE EXISTS (SELECT 1 FROM tickets u WHERE u.ticket_id = t.ticket_id"
                  " AND (u.ts > t.ts OR (u.ts = t.ts AND u.id > t.id)))")
    for r in dup:
        STORE.x("DELETE FROM tickets WHERE id=?", (r["id"],))
    out = {"handoffs_deleted": len(race), "tickets_deleted": len(dup)}
    if body and body.since is not None:
        # audit_iter_*.py runs C1 as the demo mechanic: its tickets and handoffs (TKT-0001…0009 on M333YK47 by 27.11) are
        # removed after the run; the account is required so nobody else's records go
        if not body.user_id:
            raise HTTPException(400, "user_id")
        cond, args = "ts >= ? AND user_id = ?", [body.since, body.user_id]
        if body.vehicle_id:
            cond += " AND vehicle_id = ?"; args.append(body.vehicle_id)
        tk = [r for r in STORE.q(f"SELECT id, ticket_id FROM tickets WHERE {cond}", tuple(args)) if r["ticket_id"] not in body.keep_ticket_ids]
        hd = [r for r in STORE.q(f"SELECT id FROM handoffs WHERE {cond}", tuple(args)) if r["id"] not in body.keep_handoff_ids]
        for r in tk:
            STORE.x("DELETE FROM tickets WHERE id=?", (r["id"],))
        for r in hd:
            STORE.x("DELETE FROM handoffs WHERE id=?", (r["id"],))
        out["audit_tickets_deleted"], out["audit_handoffs_deleted"] = len(tk), len(hd)
    STORE.audit(user_id=me_["id"], role=me_["role"], action="cleanup_test_data", input=json.dumps(out), ok=1)
    return out


@app.post("/api/eng/companies")
def eng_company_create(body: CompanyIn, uid: str | None = Cookie(None)) -> dict:
    me_ = user_of(uid, roles=("engineer",))
    name = body.name.strip()[:80]
    if not name or body.llm_primary not in LLM_PRIMARY_OPTS:
        raise HTTPException(400, "name / llm_primary")
    cid = "c" + uuid.uuid4().hex[:8]
    STORE.x("INSERT INTO companies(id, name, created, llm_primary, note) VALUES (?,?,?,?,?)", (cid, name, time.time(), body.llm_primary, body.note[:300]))
    STORE.audit(user_id=me_["id"], role=me_["role"], action="company_create", input=f"{cid} {name}", ok=1)
    return {"company": _company_view(STORE.one("SELECT * FROM companies WHERE id=?", (cid,)))}


@app.post("/api/eng/companies/{cid}")
def eng_company_edit(cid: str, body: CompanyPatch, uid: str | None = Cookie(None)) -> dict:
    me_ = user_of(uid, roles=("engineer",))
    if not STORE.one("SELECT id FROM companies WHERE id=?", (cid,)):
        raise HTTPException(404)
    if body.delete:
        if cid == tenancy.SEED_ID:
            raise HTTPException(400, "the seed company stays")
        people = [r["id"] for r in STORE.q("SELECT id FROM users WHERE company_id=?", (cid,))]
        for p_ in people:
            STORE.delete_user_data(p_)
        STORE.x("DELETE FROM company_vehicles WHERE company_id=?", (cid,))
        STORE.x("DELETE FROM companies WHERE id=?", (cid,))
        STORE.audit(user_id=me_["id"], role=me_["role"], action="company_delete", input=f"{cid}, people {len(people)}", ok=1)
        return {"deleted": cid, "people": len(people)}
    if body.name is not None and body.name.strip():
        STORE.x("UPDATE companies SET name=? WHERE id=?", (body.name.strip()[:80], cid))
    if body.llm_primary is not None:
        if body.llm_primary not in LLM_PRIMARY_OPTS:
            raise HTTPException(400, "llm_primary")
        STORE.x("UPDATE companies SET llm_primary=? WHERE id=?", (body.llm_primary, cid))
    if body.active is not None:
        STORE.x("UPDATE companies SET active=? WHERE id=?", (int(body.active), cid))
    if body.note is not None:
        STORE.x("UPDATE companies SET note=? WHERE id=?", (body.note[:300], cid))
    if body.vehicles is not None:
        bad = [v for v in body.vehicles if v not in AGENT.fleet.all_ids()]
        if bad:
            raise HTTPException(400, f"unknown vehicles: {', '.join(bad)}")
        for v in body.vehicles:
            STORE.x("INSERT OR REPLACE INTO company_vehicles(vehicle_id, company_id) VALUES (?,?)", (v, cid))
    STORE.audit(user_id=me_["id"], role=me_["role"], action="company_edit", input=f"{cid} {sorted(k for k, v in body.model_dump().items() if v is not None)}", ok=1)
    return {"company": _company_view(STORE.one("SELECT * FROM companies WHERE id=?", (cid,)))}


@app.post("/api/eng/users/{target}")
def eng_user_edit(target: str, body: EngUserPatch, uid: str | None = Cookie(None)) -> dict:
    """The engineer may switch off or remove any account (not himself, not the fixed demo accounts)."""
    me_ = user_of(uid, roles=("engineer",))
    u = STORE.user(target)
    if not u:
        raise HTTPException(404)
    if target == me_["id"] or target.startswith("demo-"):
        raise HTTPException(400, "not this account")
    if body.delete:
        STORE.delete_user_data(target)
    elif body.active is not None:
        STORE.upsert_user(target, active=int(body.active))
        if not body.active:
            STORE.x("UPDATE sessions SET revoked=1 WHERE user_id=?", (target,))
            STORE.x("UPDATE login_tokens SET revoked=1 WHERE user_id=?", (target,))
    STORE.audit(user_id=me_["id"], role=me_["role"], action="user_delete" if body.delete else "user_active", input=target, ok=1)
    return {"ok": True, "deleted": body.delete}


@app.post("/api/eng/companies/{cid}/chief")
def eng_company_chief(cid: str, body: ChiefIn, uid: str | None = Cookie(None)) -> dict:
    """The engineer creates the company's chief mechanic (name, PIN, how to talk to him). The chief then creates his own staff."""
    me_ = user_of(uid, roles=("engineer",))
    if not STORE.one("SELECT id FROM companies WHERE id=?", (cid,)):
        raise HTTPException(404)
    pin = body.pin.strip() or _new_pin()
    if not re.fullmatch(r"\d{4,8}", pin):
        raise HTTPException(400, "PIN: 4-8 digits")
    name = pii.mask(body.name.strip()[:60]) or "—"
    new = "u" + uuid.uuid4().hex[:10]
    login = f"{re.sub(r'[^a-z0-9]', '', name.lower().translate(str.maketrans('абвгдеёжзийклмнопрстуфхцчшщъыьэюя', 'abvgdeejziiklmnoprstufhccss_y_eua'))) or 'chief'}{secrets_suffix()}"
    u = STORE.upsert_user(new, name=name, role="manager", experience="expert", title=body.title[:80], vehicles="[]", lang="ru", country="RU", login=login,
                          pin_hash=_pin_hash(pin), active=1, onboarded=0 if not (body.style or body.formality) else 1, created_by=me_["id"],
                          company_id=cid, email=_email_or_none(body.email))
    if body.style or body.formality in ("ty", "vy"):
        p = uprofile.load(u)
        ustyle.set_text(p, u, pii.mask(body.style) if body.style.strip() else ustyle.seed_text(u, body.formality or None), "engineer")
        STORE.upsert_user(new, profile=uprofile.dump(p))
    STORE.audit(user_id=me_["id"], role=me_["role"], action="company_chief_create", input=f"{cid} {login}", ok=1)
    return {"user": _user_row(STORE.user(new)), "login": login, "pin": pin, "invite": f"/?login={login}"}


# ---------------------------------------------------------------- device linking by QR: a 24 h link + the person's PIN
QR_TTL = int(os.environ.get("QR_TTL_S", str(24 * 3600)))
QR_MAX_FAILS = 5


def _public_base(request: Request) -> str:
    """Origin the QR should point to: PUBLIC_URL if set, else the host the user came through (https behind a tunnel/proxy)."""
    if os.environ.get("PUBLIC_URL"):
        return os.environ["PUBLIC_URL"]
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto.split(',')[0].strip()}://{host}/"


def _qr_sig(tok: str) -> str:
    return hmac.new(_cookie_key(), ("qr:" + tok).encode(), hashlib.sha256).hexdigest()[:16]


def _qr_hash(tok: str) -> str:
    return hashlib.sha256(("dm-qr:" + tok).encode()).hexdigest()


def _may_issue_for(me_: dict, target: dict) -> bool:
    if me_["id"] == target["id"] or me_["role"] == "engineer":
        return True
    return me_["role"] == "manager" and target["role"] != "engineer" and tenancy.company_of(target) == tenancy.company_of(me_)


def _pin_of(u: dict) -> tuple[str | None, str | None]:
    """(hash, plain demo PIN): what the QR screen checks."""
    a = next((a for a in DEMO.get("accounts", []) if a["uid"] == u["id"]), None)
    return u.get("pin_hash"), (a or {}).get("pin")


class QrIn(BaseModel):
    user_id: str | None = None
    email: str | None = None


def _qr_target(me_: dict, user_id: str | None) -> dict:
    t = STORE.user(user_id) if user_id else me_
    if not t or not _may_issue_for(me_, t):
        raise HTTPException(404, "no such user")
    if not any(_pin_of(t)):
        raise HTTPException(400, "this account has no PIN")
    return t


def _qr_issue(me_: dict, t: dict, base: str) -> dict:
    import secrets
    import segno
    STORE.x("UPDATE login_tokens SET revoked=1 WHERE user_id=? AND revoked=0", (t["id"],))  # one live link per person
    tok = secrets.token_urlsafe(18)
    exp = time.time() + QR_TTL
    STORE.x("INSERT INTO login_tokens(hash, user_id, created, expires, issued_by) VALUES (?,?,?,?,?)", (_qr_hash(tok), t["id"], time.time(), exp, me_["id"]))
    link = f"{base.rstrip('/')}/q/{tok}.{_qr_sig(tok)}"
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="png", scale=8, border=2)
    STORE.audit(user_id=me_["id"], role=me_["role"], action="qr_issue", input=t["id"][:8], ok=1)
    return {"link": link, "expires": exp, "qr_png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), "user": t["name"], "email": t.get("email")}


@app.post("/api/qr/issue")
def qr_issue(body: QrIn, request: Request, uid: str | None = Cookie(None)) -> dict:
    """New QR for me (or, for the chief / engineer, for a person of the staff). The previous link stops working."""
    me_ = user_of(uid)
    return _qr_issue(me_, _qr_target(me_, body.user_id), _public_base(request))


@app.post("/api/qr/revoke")
def qr_revoke(body: QrIn, uid: str | None = Cookie(None)) -> dict:
    me_ = user_of(uid)
    t = _qr_target(me_, body.user_id)
    n = STORE.x("UPDATE login_tokens SET revoked=1 WHERE user_id=? AND revoked=0", (t["id"],)).rowcount
    return {"ok": True, "revoked": n}


def _smtp_ready() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_mail(to: str, subject: str, text: str, png: bytes) -> None:
    """SMTP from .env: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SSL=1 for port 465."""
    import smtplib
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = os.environ["SMTP_FROM"], to, subject
    m.set_content(text)
    m.add_attachment(png, maintype="image", subtype="png", filename="qr.png")
    port = int(os.environ.get("SMTP_PORT", "587"))
    cls = smtplib.SMTP_SSL if os.environ.get("SMTP_SSL") == "1" or port == 465 else smtplib.SMTP
    with cls(os.environ["SMTP_HOST"], port, timeout=20) as srv:
        if cls is smtplib.SMTP:
            srv.starttls()
        if os.environ.get("SMTP_USER"):
            srv.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        srv.send_message(m)


@app.post("/api/qr/email")
def qr_email(body: QrIn, request: Request, uid: str | None = Cookie(None)) -> dict:
    """«Отправить на почту»: a fresh QR + link to the person's own e-mail (from the profile). Without SMTP: mailto + PNG download."""
    me_ = user_of(uid)
    t = _qr_target(me_, body.user_id)
    if body.email is not None and (me_["id"] == t["id"] or me_["role"] in ("manager", "engineer")):
        if not _email_or_none(body.email):
            raise HTTPException(400, "e-mail")
        t = STORE.upsert_user(t["id"], email=_email_or_none(body.email))
    if not t.get("email"):
        raise HTTPException(400, "no e-mail in the profile")
    q = _qr_issue(me_, t, _public_base(request))
    text = (f"Вход в «Цифровой механик» на новом устройстве: откройте ссылку или наведите камеру на QR-код во вложении, затем введите свой PIN.\n\n"
            f"{q['link']}\n\nСсылка действует 24 часа. Если вы её не запрашивали, сообщите главному механику.")
    if not _smtp_ready():
        from urllib.parse import quote
        return {**q, "sent": False, "mailto": f"mailto:{t['email']}?subject={quote('Вход в «Цифровой механик»')}&body={quote(text)}"}
    try:
        send_mail(t["email"], "Вход в «Цифровой механик»", text, base64.b64decode(q["qr_png"].split(",", 1)[1]))
    except Exception as e:  # noqa: BLE001 - mail server down: the person can still use the QR on screen
        jlog(event="smtp_error", error=type(e).__name__)
        raise HTTPException(502, "mail server error")
    STORE.audit(user_id=me_["id"], role=me_["role"], action="qr_email", input=t["id"][:8], ok=1)
    return {**q, "sent": True}


def _qr_row(token: str) -> dict | None:
    tok, _, sig = (token or "").partition(".")
    if not tok or not hmac.compare_digest(sig, _qr_sig(tok)):
        return None
    r = STORE.one("SELECT * FROM login_tokens WHERE hash=?", (_qr_hash(tok),))
    if not r or r["revoked"] or r["expires"] < time.time():
        return None
    return r


_QR_T: dict = {}


def _qr_t(lang: str | None) -> dict:
    if not _QR_T:
        _QR_T.update(json.loads((STATIC / "qr_i18n.json").read_text(encoding="utf-8")))
    return {**_QR_T["ru"], **_QR_T.get(lang or "ru", {})}


@app.get("/q/{token}", response_class=HTMLResponse)
def qr_page(token: str, request: Request) -> HTMLResponse:
    """The QR opens this screen: whose link it is, and a PIN field. Nothing happens without the PIN."""
    r = _qr_row(token)
    u = STORE.user(r["user_id"]) if r else None
    if not u or not u.get("active", 1):
        lang = (request.headers.get("accept-language") or "ru")[:2].lower()
        t = _qr_t(lang)
        return HTMLResponse(QR_HTML.replace("{{lang}}", lang if lang in _QR_T else "ru").replace("{{body}}", f"<h1>{t['p_title']}</h1><p>{html.escape(t['p_gone'])}</p>"), status_code=410)
    lang = u.get("lang") or "ru"
    t = _qr_t(lang)
    body = (f"<h1>{html.escape(t['p_title'])}</h1><p>{html.escape(u['name'])}</p>"
            f"<form id='f'><label for='pin'>{html.escape(t['p_pin'])}</label><input id='pin' inputmode='numeric' autocomplete='one-time-code' pattern='[0-9]*' maxlength='8' required autofocus>"
            f"<button>{html.escape(t['p_go'])}</button><p id='e' class='err'></p></form>"
            f"<script>document.getElementById('f').onsubmit=async e=>{{e.preventDefault();const r=await fetch('/api/qr/redeem',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
            f"body:JSON.stringify({{token:{json.dumps(token)},pin:document.getElementById('pin').value}})}});const d=await r.json().catch(()=>({{}}));"
            f"if(r.ok)location.href=d.redirect;else document.getElementById('e').textContent=r.status===429?{json.dumps(t['p_locked'], ensure_ascii=False)}:r.status===410?{json.dumps(t['p_gone'], ensure_ascii=False)}:{json.dumps(t['p_bad'], ensure_ascii=False)};}}</script>")
    return HTMLResponse(QR_HTML.replace("{{lang}}", lang if lang in _QR_T else "ru").replace("{{body}}", body))


QR_HTML = """<!doctype html><html lang="{{lang}}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход · Цифровой механик</title><link rel="stylesheet" href="/static/app.css"><style>main{max-width:360px;margin:12vh auto;padding:24px}
form{display:flex;flex-direction:column;gap:12px}input{min-height:52px;font-size:1.4rem;letter-spacing:.3em;text-align:center;border-radius:12px;border:1px solid var(--border);background:var(--surface-2);color:var(--text)}
button{min-height:52px;border-radius:12px;border:0;background:var(--accent);color:#fff;font-weight:600;font-size:1rem}.err{color:var(--danger);min-height:1.2em}</style></head>
<body><main>{{body}}</main></body></html>"""


class QrRedeemIn(BaseModel):
    token: str
    pin: str


@app.post("/api/qr/redeem")
def qr_redeem(body: QrRedeemIn, response: Response) -> dict:
    """Link + the person's PIN -> the usual long session on this device. The link stays valid for other devices until it expires."""
    r = _qr_row(body.token)
    u = STORE.user(r["user_id"]) if r else None
    if not u or not u.get("active", 1):
        raise HTTPException(410, "link expired")
    if r["locked_until"] and r["locked_until"] > time.time():
        raise HTTPException(429, "too many attempts")
    h, plain = _pin_of(u)
    ok = (bool(h) and _pin_ok(body.pin, h)) or (bool(plain) and hmac.compare_digest(body.pin.strip(), plain))
    if not ok:
        fails = (r["fails"] or 0) + 1
        STORE.x("UPDATE login_tokens SET fails=?, locked_until=? WHERE hash=?", (0 if fails >= QR_MAX_FAILS else fails, time.time() + 600 if fails >= QR_MAX_FAILS else 0, r["hash"]))
        STORE.audit(user_id=u["id"], role=u["role"], action="qr_login", ok=0)
        raise HTTPException(429 if fails >= QR_MAX_FAILS else 403, "too many attempts" if fails >= QR_MAX_FAILS else "wrong PIN")
    STORE.x("UPDATE login_tokens SET fails=0, used=? WHERE hash=?", (time.time(), r["hash"]))
    u = STORE.upsert_user(u["id"], consent_at=u.get("consent_at") or time.time())
    start_session(response, u["id"])
    set_profile_cookie(response, u)
    STORE.audit(user_id=u["id"], role=u["role"], action="qr_login", ok=1)
    return {"ok": True, "redirect": {"manager": "/manager", "engineer": "/engineer"}.get(u["role"], "/")}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    jlog(event="error", path=request.url.path, error=type(exc).__name__, detail=str(exc)[:300])
    return JSONResponse(status_code=500, content={"detail": "internal error"})
