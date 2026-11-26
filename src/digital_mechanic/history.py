"""Per-vehicle diagnostic timeline: checks + tickets + repairs + parts + re-check, recurring faults, days since last check.

Sources: official ML stub history (vehicles.json), the app's own SQLite (diagnoses run, tickets, repairs, answers),
and an optional demo seed (data/demo_history.json) for showing a richer history. Mechanic names are masked (152-FZ).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

STATUS_RU = {"normal": "норма", "anomaly": "отклонение", "check_failed": "сбой записи"}


def mask_name(name: str | None) -> str | None:
    """'Сидоров П.' -> 'С***в П.' ; one-letter names stay hidden."""
    if not name or name == "—":
        return None
    parts = name.split()
    first = parts[0]
    masked = first[0] + "***" + (first[-1] if len(first) > 3 else "")
    return " ".join([masked] + [p[0] + "." for p in parts[1:] if p])


def demo_mileage(vehicle_id: str, checked_at: str | None, first_at: str | None) -> int | None:
    """Deterministic demo odometer (stubs have no mileage): base per vehicle + ~110 km/day."""
    if not checked_at or not first_at:
        return None
    base = 40_000 + int(hashlib.md5(vehicle_id.encode()).hexdigest()[:6], 16) % 260_000
    days = (date.fromisoformat(checked_at[:10]) - date.fromisoformat(first_at[:10])).days
    return int(round((base + days * 110) / 10) * 10)


def load_demo(path: str | Path) -> dict[str, list[dict]]:
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for e in json.loads(p.read_text(encoding="utf-8")).get("events", []):
        out.setdefault(e["vehicle_id"], []).append(e)
    return out


def timeline(vehicle, sessions: list, store, demo: dict[str, list[dict]], today: date | None = None, until: str | None = None) -> dict:
    vid = vehicle.vehicle_id
    sessions = sorted(sessions, key=lambda s: s.checked_at or "")
    if until:
        sessions = [s for s in sessions if (s.checked_at or "") <= until]
    first = sessions[0].checked_at if sessions else None
    tickets = store.tickets(vid)
    repairs = store.q("SELECT * FROM repairs WHERE vehicle_id=? ORDER BY id", (vid,))
    runs = store.q("SELECT d.ts, d.session_id, d.user_id, u.name FROM diagnoses d LEFT JOIN users u ON u.id=d.user_id WHERE d.vehicle_id=? ORDER BY d.ts", (vid,))
    answers = store.q("SELECT ts, content, meta FROM messages WHERE vehicle_id=? AND role='assistant' AND content != '' ORDER BY ts", (vid,))
    demo_ev = {e["after_session"]: e for e in demo.get(vid, [])}
    entries = []
    for i, s in enumerate(sessions):
        st = getattr(s.status, "value", s.status)
        e = {"session_id": s.session_id, "date": (s.checked_at or "")[:10], "checked_at": s.checked_at,
             "mileage_km": demo_mileage(vid, s.checked_at, first), "status": st, "status_ru": STATUS_RU.get(st, st),
             "anomaly_type": s.anomaly_type, "component": s.anomaly_title, "anomaly_score": s.anomaly_score,
             "confidence": s.confidence, "signal_quality": getattr(s.signal_quality, "value", s.signal_quality),
             "description": s.description, "ticket": None, "repair": None, "parts": [], "recheck": None,
             "mechanic": None, "answer": None, "sources": [], "demo": False}
        run = next((r for r in runs if r["session_id"] == s.session_id), None)
        if run:
            e["mechanic"] = mask_name(run["name"])
            nxt = next((r["ts"] for r in runs if r["ts"] > run["ts"]), None)
            ans = [a for a in answers if a["ts"] >= run["ts"] and (nxt is None or a["ts"] < nxt)]
            withsrc = [a for a in ans if a["meta"] and json.loads(a["meta"]).get("sources")]
            pick = (withsrc or ans or [None])[0]
            if pick:
                e["answer"] = pick["content"][:2000]
                e["sources"] = json.loads(pick["meta"]).get("sources", []) if pick["meta"] else []
        tk = next((t for t in tickets if t["session_id"] == s.session_id), None)
        if tk:
            payload = tk.get("payload") or {}
            payload = json.loads(payload) if isinstance(payload, str) else payload
            rev = tk.get("review")
            rev = json.loads(rev) if isinstance(rev, str) else rev
            e["ticket"] = {"ticket_id": tk["ticket_id"], "status": tk.get("status") or "created", "urgency": tk.get("urgency"),
                           "review_comment": (rev or {}).get("comment") or None}
            e["parts"] = payload.get("spare_parts", [])
        rp = next((r for r in repairs if r["session_id"] == s.session_id), None)
        if rp:
            steps = json.loads(rp["steps"]) if isinstance(rp["steps"], str) else rp["steps"]
            done = [st_.get("text", "") if isinstance(st_, dict) else str(st_) for st_ in steps[: rp["current"] if rp["status"] != "done" else len(steps)]]
            e["repair"] = {"status": rp["status"], "done": len(done), "total": len(steps), "steps_done": done}
        if s.session_id in demo_ev and not tk:
            d = demo_ev[s.session_id]
            e["ticket"] = {"ticket_id": d["ticket_id"], "status": d.get("ticket_status", "closed"), "urgency": None}
            e["repair"] = {"status": "done", "done": len(d["repair_steps"]), "total": len(d["repair_steps"]), "steps_done": d["repair_steps"], "date": d.get("date")}
            e["parts"] = d.get("parts", [])
            e["mechanic"] = e["mechanic"] or mask_name(d.get("mechanic"))
            e["demo"] = True
        if (e["ticket"] or e["repair"]) and i + 1 < len(sessions):
            n = sessions[i + 1]
            e["recheck"] = {"session_id": n.session_id, "date": (n.checked_at or "")[:10], "status": getattr(n.status, "value", n.status),
                            "status_ru": STATUS_RU.get(getattr(n.status, "value", n.status)), "anomaly_type": n.anomaly_type, "anomaly_score": n.anomaly_score}
        entries.append(e)
    comp = Counter(e["anomaly_type"] for e in entries if e["status"] == "anomaly" and e["anomaly_type"])
    titles = {e["anomaly_type"]: e["component"] for e in entries if e["anomaly_type"]}
    recurring = [{"anomaly_type": k, "component": titles.get(k), "count": n} for k, n in comp.items() if n >= 2]
    for e in entries:
        e["recurring"] = any(r["anomaly_type"] == e["anomaly_type"] for r in recurring)
    last = entries[-1]["date"] if entries else None
    today = today or date.today()
    trend = [{"session_id": e["session_id"], "date": e["date"], "status": e["status"], "anomaly_score": e["anomaly_score"]} for e in entries]
    media = [{"id": m["id"], "date": time.strftime("%Y-%m-%d", time.localtime(m["ts"])), "ts": m["ts"], "kind": m["kind"],
              "kind_ru": MEDIA_RU.get(m["kind"], m["kind"]), "files": m["files"], "description": (m["description"] or "")[:600],
              "features": m["features"], "note": m["note"]}
             for m in (store.media(vid) if hasattr(store, "media") else [])]
    return {"vehicle_id": vid, "engine_model": vehicle.engine_model, "entries": list(reversed(entries)), "trend": trend, "media": media,
            "recurring": recurring, "last_check": last,
            "days_since_last_check": (today - date.fromisoformat(last)).days if last else None,
            "mileage_note": "пробег — демо-данные"}


MEDIA_RU = {"photo": "фото", "video": "видео", "sound": "запись звука"}


def summary_for_agent(tl: dict, limit: int = 6) -> str:
    """Compact Russian text for the agent context (B1 memory) and chips."""
    lines = []
    for m in tl.get("media", [])[:3]:
        lines.append(f"МЕДИА ОТ МЕХАНИКА {m['date']} ({m['kind_ru']}): {m['description'][:300]}")
    if tl.get("recurring"):
        lines.append("ПОВТОРЯЮЩИЕСЯ ПРОБЛЕМЫ: " + "; ".join(f"{r['anomaly_type']} {r['component']} — {r['count']} раза" for r in tl["recurring"]))
    for e in tl["entries"][:limit]:
        s = f"{e['date']} {e['session_id']}: {e['status_ru']}"
        if e["anomaly_type"]:
            s += f" {e['anomaly_type']} ({e['component']}), score {e['anomaly_score']}"
        if e["mileage_km"]:
            s += f", пробег ~{e['mileage_km']} км"
        if e["ticket"]:
            s += f"; заявка {e['ticket']['ticket_id']} ({e['ticket']['status']})"
        if e["repair"]:
            s += f"; ремонт {e['repair']['done']}/{e['repair']['total']}: " + ", ".join(e["repair"]["steps_done"][:4])
        if e["parts"]:
            s += "; запчасти: " + ", ".join(e["parts"])
        if e["recheck"]:
            s += f"; повторная проверка {e['recheck']['session_id']}: {e['recheck']['status_ru']}"
        lines.append(s)
    return "\n".join(lines)
