"""End-to-end scenario run against a live app (C1-C4). Usage: python scripts/e2e.py [base_url]"""
import json, sys, time
import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
c = httpx.Client(base_url=B, timeout=240)


def show(tag, r):
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if isinstance(d, dict):
        txt = d.get("text") or ""
        print(f"\n=== {tag} [{r.status_code}] {d.get('latency_ms', '')}ms")
        if txt:
            print(txt[:1800])
        for k in ("cards",):
            for cd in d.get(k, []) or []:
                print("  CARD", cd.get("kind"), json.dumps({x: cd.get(x) for x in ("status", "urgency", "light_text", "ticket_id", "requires_inspection", "current")}, ensure_ascii=False))
        if d.get("sources"):
            print("  SOURCES", [(s["document"], s["page"]) for s in d["sources"]])
        if d.get("tools"):
            print("  TOOLS", [(t["tool"], t.get("hits") or t.get("error") or "") for t in d["tools"]])
    return d


r = c.post("/api/login/demo", json={"account": "student", "pin": "3003", "lang": sys.argv[2] if len(sys.argv) > 2 else "ru", "consent": True}); r.raise_for_status()
V, SID = "M333YK47", "S-0005"
d = show("diagnose C1", c.post("/api/diagnose", json={"vehicle_id": V, "session_id": SID}))
print(json.dumps(d["card"]["check"], ensure_ascii=False), d["card"].get("urgency"), d["card"].get("light_text"))
show("explain", c.post("/api/chat", json={"vehicle_id": V, "event": "explain_diagnosis"}))
show("C2 drive", c.post("/api/chat", json={"vehicle_id": V, "text": "Можно ли своим ходом перегнать машину на базу?"}))
show("torque", c.post("/api/chat", json={"vehicle_id": V, "text": "Какой момент затяжки крышек коренных подшипников?"}))
d = show("ticket", c.post("/api/chat", json={"vehicle_id": V, "text": "Создать заявку"}))
draft = next((x for x in d.get("cards", []) if x["kind"] == "ticket_draft"), None)
if draft:
    print(json.dumps(draft["request"], ensure_ascii=False, indent=1))
    show("confirm", c.post("/api/ticket/confirm", json={"vehicle_id": V, "draft_id": draft["draft_id"]}))
show("repair", c.post("/api/chat", json={"vehicle_id": V, "text": "Начать ремонт по шагам"}))
show("next", c.post("/api/chat", json={"vehicle_id": V, "text": "готово"}))

# C3: a failed recording must not become a diagnosis or a ticket.
V3, SID3 = "P456AT178", "S-0003"
d3 = show("diagnose C3", c.post("/api/diagnose", json={"vehicle_id": V3, "session_id": SID3}))
check3 = (d3.get("card") or {}).get("check") or {}
print("C3 status:", check3.get("status"), "ticket_allowed:", bool((d3.get("card") or {}).get("ticket_allowed")))

# C4: the chief sees quarantine, the priority list and the vehicle history.
m = httpx.Client(base_url=B, timeout=240)
r = m.post("/api/login/demo", json={"account": "chief", "pin": "1001", "consent": True}); r.raise_for_status()
fl = m.get("/api/fleet")
fl.raise_for_status()
ov = fl.json()
counts = ov.get("counts") or ov.get("summary") or {}
print("\n=== C4 fleet", counts)
quar = [x.get("vehicle_id") for x in ov.get("vehicles", []) if x.get("status") == "quarantine"][:5]
print("quarantine sample:", quar)
target = quar[0] if quar else V
fv = m.get(f"/api/fleet/{target}"); fv.raise_for_status()
hist = fv.json()
print("C4 vehicle:", target, "history_events:", len(hist.get("timeline") or hist.get("events") or hist.get("history") or []), "diagnoses:", len(hist.get("diagnoses_run") or []))
