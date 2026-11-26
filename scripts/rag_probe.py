"""Jury-style RAG probes: in-base norms, cross-engine traps, out-of-base refusals. Usage: python scripts/rag_probe.py [base_url]"""
import sys
import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
c = httpx.Client(base_url=B, timeout=240)
c.post("/api/login", json={"name": "Жюри", "role": "mechanic", "lang": "ru", "consent": True}).raise_for_status()
PROBES = [
    ("M333YK47", "S-0005", "Какое давление масла должно быть на холостом ходу?"),
    ("M333YK47", "S-0005", "Какой момент затяжки болтов головки блока ЗМЗ-409?"),
    ("M333YK47", "S-0005", "Какой курс доллара сегодня?"),
    ("O727CA198", "S-0051", "Какой момент затяжки болтов головки цилиндров?"),
    ("O819CY198", "S-0099", "Какой зазор в клапанах на холодном двигателе?"),
    ("Y815BM178", "S-0152", "Какой момент затяжки крышек коренных подшипников?"),
    ("Y815BM178", "S-0152", "Какой ресурс двигателя до капремонта в моточасах?"),
]
for vid, sid, q in PROBES:
    c.post("/api/diagnose", json={"vehicle_id": vid, "session_id": sid})
    d = c.post("/api/chat", json={"vehicle_id": vid, "text": q}).json()
    print(f"\n### {vid} | {q} [{d.get('latency_ms')} ms]\n{(d.get('text') or '')[:900]}")
    print("  SOURCES", [(s["document"], s["page"]) for s in d.get("sources") or []])
