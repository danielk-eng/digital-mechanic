"""Demo accounts with roles (data/demo_users.json) and the seeded "who worked on which truck".

* accounts: fixed user ids, demo PINs (shown on the login screen as demo PINs, nothing else secret);
* scans: every ML session of a mechanic's assigned truck is recorded as his diagnosis run (who scanned and when);
* work: recent jobs for memory callbacks (days_ago is relative to now, so the demo works on any day).
Idempotent: safe on every start (deb keeps its DB, Vercel starts empty)."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

ROLES = ("mechanic", "manager", "engineer")


def load(path: str | Path = "data/demo_users.json") -> dict:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"accounts": [], "work": []}


def account(cfg: dict, key: str) -> dict | None:
    return next((a for a in cfg["accounts"] if a["key"] == key), None)


def public_accounts(cfg: dict) -> list[dict]:
    return [{"key": a["key"], "name": a["name"], "role": a["role"], "title": a["title"], "pin": a["pin"]} for a in cfg["accounts"]]


def people(store, cfg: dict) -> list[dict]:
    """Demo accounts + employees the chief created (users.vehicles), active only."""
    out = [dict(a) for a in cfg["accounts"]]
    known = {a["uid"] for a in out}
    for u in (store.q("SELECT id, name, role, vehicles, active FROM users WHERE login IS NOT NULL") if store else []):
        if u["id"] in known or not u.get("active", 1):
            continue
        out.append({"uid": u["id"], "name": u["name"], "role": u["role"], "vehicles": json.loads(u["vehicles"] or "[]")})
    if store:  # chief may re-assign demo trucks too
        for a in out:
            r = store.one("SELECT vehicles, active FROM users WHERE id=?", (a["uid"],))
            if r and r.get("vehicles"):
                a["vehicles"] = json.loads(r["vehicles"])
    return out


def assigned(cfg: dict, vehicle_id: str, store=None) -> dict | None:
    return next((a for a in people(store, cfg) if vehicle_id in (a.get("vehicles") or [])), None)


def _ts(iso: str | None) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else time.time()
    except ValueError:
        return time.time()


def seed(store, fleet, cfg: dict) -> int:
    n = 0
    for a in cfg["accounts"]:
        u = store.user(a["uid"])
        if not u:
            from .agent import profile as uprofile, style as ustyle
            store.upsert_user(a["uid"], name=a["name"], role=a["role"], lang="ru", experience=a.get("experience", "experienced"),
                              consent_at=time.time(), country="RU", login=a["key"], title=a.get("title"), onboarded=1, active=1,
                              vehicles=json.dumps(a.get("vehicles") or []))
            p = uprofile.load({"profile": ""})
            p["persona"] = a.get("persona") or a["role"]
            p["terms"] = {k.lower(): 3 for k in a.get("slang") or [] if k.lower() in uprofile.GLOSSARY}
            ustyle.card(p, {**a, "_persona": p["persona"]})
            store.upsert_user(a["uid"], profile=uprofile.dump(p))
        for i, smp in enumerate(cfg.get("samples", {}).get(a.get("persona") or "", [])):  # conversation samples per persona
            cid = f"{a['uid']}:{smp['vehicle_id']}"
            if store.one("SELECT 1 FROM messages WHERE conv=? AND content=?", (cid, smp["q"])):
                continue
            ts = time.time() - 86400 * (6 - 2 * i)
            src = [{"label": f"S{k + 1}", "extra": False, "image": None, **r} for k, r in enumerate(smp["src"])]
            store.x("INSERT INTO messages(ts,conv,user_id,vehicle_id,role,content,meta) VALUES (?,?,?,?,?,?,?)", (ts, cid, a["uid"], smp["vehicle_id"], "user", smp["q"], "{}"))
            store.x("INSERT INTO messages(ts,conv,user_id,vehicle_id,role,content,meta) VALUES (?,?,?,?,?,?,?)",
                    (ts + 20, cid, a["uid"], smp["vehicle_id"], "assistant", smp["a"], json.dumps({"sources": src, "seed": True}, ensure_ascii=False)))
        for vid in a.get("vehicles") or []:
            try:
                sessions = fleet.sessions(vid)
            except KeyError:
                continue
            for s in sessions:
                if store.one("SELECT 1 FROM diagnoses WHERE vehicle_id=? AND session_id=? AND decision='seed_scan'", (vid, s.session_id)):
                    continue
                d = s.model_dump(mode="json")
                store.x("INSERT INTO diagnoses(ts,user_id,vehicle_id,session_id,status,anomaly_type,anomaly_score,confidence,signal_quality,decision,urgency,payload) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (_ts(s.checked_at), a["uid"], vid, s.session_id, d["status"], d.get("anomaly_type"), d.get("anomaly_score"), d.get("confidence"),
                         d.get("signal_quality"), "seed_scan", None, json.dumps(d, ensure_ascii=False)))
                n += 1
    return n


def scanned_by(store, vehicle_id: str, session_id: str) -> dict | None:
    r = store.one("SELECT d.ts, d.user_id, u.name, u.role FROM diagnoses d LEFT JOIN users u ON u.id=d.user_id "
                  "WHERE d.vehicle_id=? AND d.session_id=? ORDER BY d.ts LIMIT 1", (vehicle_id, session_id))
    return r if r and r.get("user_id") else None
