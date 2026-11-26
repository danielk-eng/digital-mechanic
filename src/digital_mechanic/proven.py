"""Proven fixes in this fleet: past repairs on vehicles with the SAME engine and the SAME fault code.

Sources (no generated text): our confirmed tickets and finished repairs (steps carry manual page refs), and the
demo history events (synthetic past tickets of this fleet, shown with their ticket id). A fix counts as verified
when the next acoustic check of that vehicle after the repair was normal.
"""

from __future__ import annotations

import json
import time


def _next_normal(sessions, after_session: str | None, after_ts: str | None = None) -> bool | None:
    ss = sorted(sessions, key=lambda s: s.checked_at or "")
    idx = next((i for i, s in enumerate(ss) if s.session_id == after_session), None)
    later = ss[idx + 1:] if idx is not None else [s for s in ss if after_ts and (s.checked_at or "") > after_ts]
    if not later:
        return None
    return getattr(later[0].status, "value", later[0].status) == "normal"


def proven_fixes(agent, vehicle_id: str, code: str | None, limit: int = 5) -> list[dict]:
    if not code:
        return []
    eng = agent.fleet.get(vehicle_id).engine_model
    peers = [v for v in agent.fleet.ids() if agent.fleet.get(v).engine_model == eng]
    out = []
    for vid in peers:
        sessions = agent.fleet.sessions(vid)
        by_sid = {s.session_id: s for s in sessions}
        # 1) our tickets / repairs
        for t in agent.store.tickets(vid):
            if t["anomaly_type"] != code:
                continue
            rep = agent.store.one("SELECT * FROM repairs WHERE ticket_id=? OR (vehicle_id=? AND session_id=?) ORDER BY id DESC LIMIT 1",
                                  (t["ticket_id"], vid, t["session_id"]))
            steps = json.loads(rep["steps"]) if rep else [{"text": o.get("text"), "doc_id": o.get("doc_id"), "page": o.get("page"),
                                                            "document": o.get("document")} for o in t["payload"].get("operations", [])]
            out.append({"vehicle_id": vid, "date": time.strftime("%Y-%m-%d", time.localtime(t["ts"])), "ticket_id": t["ticket_id"],
                        "status": (rep or {}).get("status") or t.get("status"), "source": "fleet",
                        "steps": [{"text": s.get("text"), "doc_id": s.get("doc_id"), "page": s.get("page"), "document": s.get("document")} for s in steps][:8],
                        "parts": [p.get("name") or p.get("text") for p in t["payload"].get("spare_parts", [])][:5],
                        "verified": _next_normal(sessions, t["session_id"])})
        # 2) demo history (synthetic past tickets of this fleet)
        for e in agent.demo.get(vid, []):
            s = by_sid.get(e.get("after_session"))
            if not s or s.anomaly_type != code:
                continue
            out.append({"vehicle_id": vid, "date": e.get("date"), "ticket_id": e.get("ticket_id"), "status": e.get("ticket_status"), "source": "demo",
                        "steps": [{"text": x} for x in e.get("repair_steps", [])][:8], "parts": e.get("parts", [])[:5],
                        "verified": _next_normal(sessions, e.get("after_session"))})
    out.sort(key=lambda x: (x["verified"] is not True, x["vehicle_id"] == vehicle_id, x["date"] or ""), reverse=False)
    return out[:limit]
