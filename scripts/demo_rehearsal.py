"""Video take (docs/ВИДЕО_СЦЕНАРИЙ.md): seed the demo state and time every scene through the same API the UI uses.

    python scripts/demo_rehearsal.py prep   --url https://<stand>   # Иванов А. starts the E549YT198 repair, 2 steps done
    python scripts/demo_rehearsal.py dryrun --url https://<stand>   # every scene, latency per step, PASS/FAIL per check
Results: runs/demo_dryrun_<time>.json. No secrets: demo accounts with their published demo PINs only.
"""
import argparse, json, re, sys, time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MAIN = ("B418HK98", "S-0009")          # ЯМЗ-236М2, F01 клапанный механизм: C1 + contradiction (valve clearance) + ticket
LOWCONF = ("M739BA198", "S-0103")      # ЗМЗ-5234.10, F01, confidence 0,48
MISMATCH = ("Y652CK98", "S-0132")      # ЗМЗ-5234.10 (petrol) with F08 (diesel)
RESUME = ("E549YT198", "S-0011")       # ЯМЗ-236М2, F02: repair started by Иванов А.
Q_VOICE = "Какой зазор в клапанах и когда его регулировать?"
Q_NODATA = "Какой момент затяжки гаек крепления турбокомпрессора?"
TICKET = "Создай заявку на ремонт"


def client(url, account, pin):
    c = httpx.Client(base_url=url, timeout=300, follow_redirects=True)
    c.post("/api/login/demo", json={"account": account, "pin": pin, "consent": True}).raise_for_status()
    return c


class Timer:
    def __init__(self):
        self.rows, self.fail = [], 0

    def step(self, name, fn, check=None):
        t = time.time()
        try:
            r = fn()
            ok, note = (check(r) if check else (True, ""))
        except Exception as e:  # report, keep going
            r, ok, note = None, False, f"{type(e).__name__}: {e}"[:200]
        dt = round(time.time() - t, 1)
        self.rows.append({"step": name, "sec": dt, "ok": bool(ok), "note": note})
        self.fail += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {dt:6.1f}s  {name}  {note}", flush=True)
        return r


def chat(c, vid, text="", event=None, sources=None, sid=None):
    body = {"vehicle_id": vid, "text": text, "event": event, "sources": sources, "session_id": sid}
    r = c.post("/api/chat", json=body)
    r.raise_for_status()
    return r.json()


def kinds(t):
    return [k.get("kind") for k in t.get("cards") or []]


def prep(url):
    v = client(url, "veteran", "3001")
    vid, sid = RESUME
    card = v.get(f"/api/vehicle/{vid}").json().get("repair")
    if card and card.get("status", "open") == "open" and card.get("steps"):
        print("repair already open:", card.get("title"), "step", (card.get("current") or 0) + 1); return
    v.post("/api/diagnose", json={"vehicle_id": vid, "session_id": sid}).raise_for_status()
    for attempt in range(3):
        t = chat(v, vid, "Начни ремонт по шагам по руководству", sid=sid)
        if "repair" in kinds(t) or v.get(f"/api/vehicle/{vid}").json().get("repair"):
            break
        print("no repair card yet:", t.get("text", "")[:160])
    for _ in range(2):
        v.post("/api/repair/action", json={"vehicle_id": vid, "action": "done"}).raise_for_status()
    card = v.get(f"/api/vehicle/{vid}").json().get("repair")
    print("repair:", card and card.get("title"), "current step", card and (card.get("current", 0) + 1), "of", card and len(card.get("steps", [])))


def dryrun(url):
    T = Timer()
    m = T.step("login Сидоров П. (PIN 3002)", lambda: client(url, "selftaught", "3002"), lambda r: (True, ""))
    vid, sid = MAIN
    T.step(f"diagnose {vid} {sid}", lambda: m.post("/api/diagnose", json={"vehicle_id": vid, "session_id": sid}).json(),
           lambda r: (r["card"]["status"] == "anomaly", r["card"].get("light_text", "")[:80]))
    T.step("explain verdict", lambda: chat(m, vid, event="explain_diagnosis", sid=sid),
           lambda r: (bool(r.get("sources")) and "[S" not in r["text"][:0], f"{len(r['text'])} chars, {len(r.get('sources') or [])} src"))
    wav = T.step("tts of the voice question (stand)", lambda: m.post("/api/tts", json={"text": Q_VOICE}).content,
                 lambda r: (len(r) > 10000, f"{len(r)} bytes"))
    if wav:
        T.step("stt (as the mic would)", lambda: m.post("/api/stt", files={"audio": ("q.wav", wav, "audio/wav")}, data={"lang": "ru"}).json(),
               lambda r: (bool(re.search(r"(?i)зазор", r.get("text", ""))), f"{r.get('engine')}: {r.get('text', '')[:70]}"))
    q = T.step("voice question -> cited answer + contradiction", lambda: chat(m, vid, Q_VOICE, sid=sid),
               lambda r: ("contradiction" in kinds(r) and bool(r.get("sources")), f"cards {kinds(r)}, src {[(s['document'][:14], s['page']) for s in r.get('sources') or []][:4]}"))
    if q:
        T.step("«Подробнее»", lambda: chat(m, vid, q["text"], event="expand", sources=q.get("sources"), sid=sid),
               lambda r: (len(r.get("text", "")) > len(q["text"]), f"{len(r.get('text', ''))} chars"))
    d = T.step("ticket draft", lambda: chat(m, vid, TICKET, sid=sid),
               lambda r: ("ticket_draft" in kinds(r), f"cards {kinds(r)}: {r.get('text', '')[:90]}"))
    card = next((k for k in (d or {}).get("cards") or [] if k.get("kind") == "ticket_draft"), None)
    if card:
        T.step("confirm with priority «высокий» (mechanic's choice)",
               lambda: m.post("/api/ticket/confirm", json={"vehicle_id": vid, "draft_id": card.get("draft_id"), "token": card.get("token"), "urgency": "high"}).json(),
               lambda r: (bool(re.search(r"TKT-\d+", json.dumps(r, ensure_ascii=False))), json.dumps(r, ensure_ascii=False)[:120]))
    vid2, sid2 = LOWCONF
    T.step(f"diagnose {vid2} {sid2} (0,48)", lambda: m.post("/api/diagnose", json={"vehicle_id": vid2, "session_id": sid2}).json(),
           lambda r: (r["card"].get("ticket_allowed") is False, r["card"].get("light_text", "")[:90]))
    T.step("explain 0,48", lambda: chat(m, vid2, event="explain_diagnosis", sid=sid2), lambda r: (bool(r.get("text")), r["text"][:90].replace("\n", " ")))
    T.step("ticket request at 0,48 -> refusal", lambda: chat(m, vid2, TICKET, sid=sid2),
           lambda r: (r["text"].startswith("Заявку не оформляю") and "ticket_draft" not in kinds(r), r["text"][:90]))
    vid3, sid3 = MISMATCH
    T.step(f"diagnose {vid3} {sid3} (F08 on petrol)", lambda: m.post("/api/diagnose", json={"vehicle_id": vid3, "session_id": sid3}).json(),
           lambda r: ("ошибка модели" in (r["card"].get("light_text") or ""), r["card"].get("light_text", "")[:90]))
    T.step("explain mismatch (no LLM)", lambda: chat(m, vid3, event="explain_diagnosis", sid=sid3),
           lambda r: ("ошибка модели" in r["text"] and not r.get("sources"), r["text"][:90]))
    T.step("out-of-manual question", lambda: chat(m, vid, Q_NODATA, sid=sid),
           lambda r: (bool(re.search(r"(?i)данных нет|нет данных|в руководстве[^.\n]{0,140}\sнет", r["text"])) and "специалист" not in r["text"].lower(), r["text"][:120].replace("\n", " ")))
    T.step("resume: open E549YT198", lambda: m.get(f"/api/vehicle/{RESUME[0]}").json(),
           lambda r: (bool(r.get("resume")) and "шаге" in r["resume"]["text"], (r.get("resume") or {}).get("text", "")[:120]))
    ch = T.step("login Александр (PIN 1001)", lambda: client(url, "chief", "1001"), lambda r: (True, ""))
    T.step("manager fleet", lambda: ch.get("/api/fleet").json(), lambda r: (bool(r), f"keys {list(r)[:5]}"))
    T.step("manager feed: «пробел в базе»", lambda: ch.get("/api/handoffs").json(),
           lambda r: (any(h["kind"] == "kb_gap" for h in r["handoffs"]), f"{sum(h['kind'] == 'kb_gap' for h in r['handoffs'])} kb_gap"))
    e = T.step("login Павел (PIN 2002)", lambda: client(url, "engineer", "2002"), lambda r: (True, ""))
    T.step("engineer companies", lambda: e.get("/api/eng/companies").json(), lambda r: (bool(r.get("companies")), f"{len(r.get('companies', []))} companies"))
    T.step("engineer health (PII badge)", lambda: e.get("/api/eng/health").json(),
           lambda r: ("Обезлич" in json.dumps(r, ensure_ascii=False) or "pii" in json.dumps(r).lower(), json.dumps(r, ensure_ascii=False)[:100]))
    T.step("cost calculator", lambda: e.get("/api/cost").json(), lambda r: (r["total"]["primary_free"] == 0, f"paid {r['total']['primary_paid']:.2f} ₽"))
    out = ROOT / "runs"; out.mkdir(exist_ok=True)
    p = out / f"demo_dryrun_{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps({"url": url, "rows": T.rows, "fail": T.fail}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {p}  fail={T.fail}")
    return T.fail


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "dryrun"])
    ap.add_argument("--url", required=True)
    a = ap.parse_args()
    sys.exit(prep(a.url) if a.cmd == "prep" else (1 if dryrun(a.url) else 0))
