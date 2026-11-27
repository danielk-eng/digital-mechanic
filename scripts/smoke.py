"""Smoke test of a running stand: login -> vehicles -> diagnose -> chat -> suggest -> history. Usage: smoke.py BASE_URL"""
import sys, time, httpx
B = sys.argv[1].rstrip("/")
c = httpx.Client(base_url=B, timeout=300, follow_redirects=True)
r = c.post("/api/login/demo", json={"account": "student", "pin": "3003", "consent": True}); r.raise_for_status()  # demo mechanic; name-only login is intentionally disabled
vs = c.get("/api/vehicles").json(); vs = vs.get("vehicles", vs) if isinstance(vs, dict) else vs
vid = vs[0]["id"] if isinstance(vs[0], dict) and "id" in vs[0] else vs[0].get("vehicle_id")
t = time.time(); d = c.post("/api/diagnose", json={"vehicle_id": vid}).json(); print("diagnose", round(time.time()-t,1), "s", str(d)[:300])
t = time.time(); a = c.post("/api/chat", json={"vehicle_id": vid, "text": "Что проверить в первую очередь?"}).json()
print("chat", round(time.time()-t,1), "s", str(a)[:600])
print("suggest", str(c.get("/api/suggest", params={"vehicle_id": vid}).json())[:300])
print("history", c.get(f"/api/history/{vid}").status_code)
