"""End-to-end smoke over the HTTP API (needs scripts/run_demo.sh running)."""
import json, sys, httpx
B = "http://127.0.0.1:8000"
def post(p, b): return httpx.post(B + p, json=b, timeout=120).json()
def show(r):
    print("TEXT:", r["text"]); c = r.get("card") or {}
    print("CARD:", json.dumps({k: v for k, v in c.items() if k in ("kind", "light", "priority", "coverage", "decision", "draft", "citations", "ticket_id", "doubt")}, ensure_ascii=False)[:1800]); print()
vid, ses = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
r = post("/api/select", {"vehicle_id": vid}); sid = r["sid"]
show(post("/api/check", {"sid": sid, "session_id": ses}))
for q in sys.argv[3:]:
    show(post("/api/chat", {"sid": sid, "text": q}))
