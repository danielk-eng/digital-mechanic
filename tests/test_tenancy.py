"""Companies (tenants): the engineer creates a company and its chief; the chief sees and manages only his company;
QR device linking: 24 h link + the person's PIN, regenerate/revoke, rate limit, e-mail without real SMTP."""
import importlib
import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    d = tmp_path_factory.mktemp("ten")
    os.environ.update(DB_PATH=str(d / "a.db"), LOG_PATH=str(d / "l.jsonl"), UPLOADS_DIR=str(d / "up"), LLM_API_KEY="", LLM_API_KEY_FILE="", EMBED_MOCKS="1")
    for k in ("SMTP_HOST", "SMTP_FROM", "SESSION_SECRET"):
        os.environ.pop(k, None)
    import digital_mechanic.web.app as mod
    importlib.reload(mod)
    yield mod
    os.environ.pop("EMBED_MOCKS", None)


def login(m, key, pin):
    c = TestClient(m.app)
    r = c.post("/api/login/demo", json={"account": key, "pin": pin, "consent": True})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(scope="module")
def world(m):
    eng = login(m, "engineer", "2002")
    cos = eng.get("/api/eng/companies").json()
    assert [c["name"] for c in cos["companies"]] == ["НейроДВС"] and len(cos["companies"][0]["vehicles"]) == 50
    b = eng.post("/api/eng/companies", json={"name": "Автопарк Б", "llm_primary": "tokenrouter"}).json()["company"]
    moved = ["O819CY198", "M739BA198"]
    assert eng.post(f"/api/eng/companies/{b['id']}", json={"vehicles": moved}).json()["company"]["vehicles"] == sorted(moved)
    ch = eng.post(f"/api/eng/companies/{b['id']}/chief", json={"name": "Петров Б.", "pin": "4321", "formality": "vy"}).json()
    assert ch["pin"] == "4321" and ch["user"]["role"] == "manager"
    chief_b = login(m, ch["login"], "4321")
    return {"eng": eng, "b": b, "moved": moved, "chief_b": chief_b, "chief_b_login": ch["login"], "chief_a": login(m, "chief", "1001")}


def test_chief_sees_only_his_company(m, world):
    a, b = world["chief_a"], world["chief_b"]
    fa = {v["vehicle_id"] for v in a.get("/api/fleet").json()["vehicles"]}
    fb = {v["vehicle_id"] for v in b.get("/api/fleet").json()["vehicles"]}
    assert fb == set(world["moved"]) and not fa & fb and len(fa) == 48
    assert b.get("/api/fleet/O727CA198").status_code == 404 and a.get("/api/fleet/O819CY198").status_code == 404
    assert b.post("/api/chat", json={"vehicle_id": "O727CA198", "text": "x"}).status_code == 404
    assert b.get("/api/history/O727CA198").status_code == 404
    assert b.post("/api/diagnose", json={"vehicle_id": "O727CA198"}).status_code == 404


def test_chief_creates_only_own_staff(m, world):
    a, b = world["chief_a"], world["chief_b"]
    assert b.post("/api/admin/users", json={"name": "Инженер", "role": "engineer"}).status_code == 400
    assert b.post("/api/admin/users", json={"name": "Сидоров", "vehicles": ["O727CA198"]}).status_code == 400  # truck of another company
    r = b.post("/api/admin/users", json={"name": "Сидоров В.", "vehicles": ["O819CY198"], "email": "s@example.com"}).json()
    sid = r["user"]["id"]
    names_a = {u["name"] for u in a.get("/api/admin/users").json()["users"]}
    names_b = {u["name"] for u in b.get("/api/admin/users").json()["users"]}
    assert "Сидоров В." in names_b and "Сидоров В." not in names_a and "Петров Б." not in names_a
    assert a.post(f"/api/admin/users/{sid}", json={"active": False}).status_code == 404
    assert a.get(f"/api/admin/users/{sid}").status_code == 404
    mech = login(m, r["login"], r["pin"])
    assert {v["vehicle_id"] for v in mech.get("/api/vehicles").json()["vehicles"]} == set(world["moved"])
    world["mech_b"] = {"id": sid, "login": r["login"], "pin": r["pin"]}


def test_engineer_sees_all_and_filters_logs(m, world):
    eng = world["eng"]
    assert len(eng.get("/api/vehicles").json()["vehicles"]) == 50
    rows = eng.get(f"/api/eng/logs?company={world['b']['id']}&limit=200").json()["rows"]
    assert rows and all(r["company_id"] == world["b"]["id"] for r in rows)
    v = {c["id"]: c for c in eng.get("/api/eng/companies").json()["companies"]}[world["b"]["id"]]
    assert v["llm_primary"] == "tokenrouter" and v["chiefs"][0]["name"] == "Петров Б." and "usage" in v
    assert world["chief_a"].get("/api/eng/companies").status_code == 403
    assert {r["user_id"] for r in world["chief_a"].get("/api/audit").json()["rows"]}  # own company log is not empty


def test_company_provider_switch(m, world):
    from digital_mechanic.agent.llm import REQUEST_PRIMARY, Llm
    from digital_mechanic.config import Settings
    l = Llm(Settings(llm_api_key="t", cloudru_api_key="c", llm_primary="tokenrouter"))
    tok = REQUEST_PRIMARY.set("cloudru")
    assert l.order("RU")[0] == "cloudru"
    REQUEST_PRIMARY.reset(tok)
    assert l.order("RU")[0] == "tokenrouter"


def _token(link):
    return link.rsplit("/q/", 1)[1]


def test_qr_needs_pin_lasts_24h_and_is_reusable(m, world):
    vet = login(m, "veteran", "3001")
    q = vet.post("/api/qr/issue", json={}).json()
    assert q["qr_png"].startswith("data:image/png;base64,") and 23 * 3600 < q["expires"] - time.time() <= 24 * 3600 + 5
    tok = _token(q["link"])
    dev = TestClient(m.app)
    page = dev.get(f"/q/{tok}")
    assert page.status_code == 200 and "PIN" in page.text and "Иванов" in page.text
    assert dev.get("/api/session").json()["user"] is None  # opening the link alone logs nobody in
    assert dev.post("/api/qr/redeem", json={"token": tok, "pin": "0000"}).status_code == 403
    r = dev.post("/api/qr/redeem", json={"token": tok, "pin": "3001"})
    assert r.status_code == 200 and r.json()["redirect"] == "/"
    assert dev.get("/api/session").json()["user"]["id"] == "demo-ivanov"
    dev2 = TestClient(m.app)  # the same link links a second device too
    assert dev2.post("/api/qr/redeem", json={"token": tok, "pin": "3001"}).status_code == 200
    assert TestClient(m.app).post("/api/qr/redeem", json={"token": tok[:-2] + "00", "pin": "3001"}).status_code == 410  # bad signature


def test_qr_expiry_regenerate_revoke_and_rate_limit(m, world):
    vet = login(m, "veteran", "3001")
    t1 = _token(vet.post("/api/qr/issue", json={}).json()["link"])
    t2 = _token(vet.post("/api/qr/issue", json={}).json()["link"])  # regenerate: the old link dies
    c = TestClient(m.app)
    assert c.post("/api/qr/redeem", json={"token": t1, "pin": "3001"}).status_code == 410 and c.get(f"/q/{t1}").status_code == 410
    for _ in range(4):
        assert c.post("/api/qr/redeem", json={"token": t2, "pin": "1111"}).status_code == 403
    assert c.post("/api/qr/redeem", json={"token": t2, "pin": "1111"}).status_code == 429
    assert c.post("/api/qr/redeem", json={"token": t2, "pin": "3001"}).status_code == 429  # locked for 10 minutes even with the right PIN
    t3 = _token(vet.post("/api/qr/issue", json={}).json()["link"])
    m.STORE.x("UPDATE login_tokens SET expires=? WHERE hash=?", (time.time() - 1, m._qr_hash(t3.split(".")[0])))
    assert c.post("/api/qr/redeem", json={"token": t3, "pin": "3001"}).status_code == 410  # older than 24 h
    t4 = _token(vet.post("/api/qr/issue", json={}).json()["link"])
    assert vet.post("/api/qr/revoke", json={}).json()["revoked"] == 1
    assert c.post("/api/qr/redeem", json={"token": t4, "pin": "3001"}).status_code == 410


def test_qr_for_staff_only_within_company(m, world):
    a, b = world["chief_a"], world["chief_b"]
    mb = world["mech_b"]
    assert a.post("/api/qr/issue", json={"user_id": mb["id"]}).status_code == 404
    q = b.post("/api/qr/issue", json={"user_id": mb["id"]}).json()
    assert TestClient(m.app).post("/api/qr/redeem", json={"token": _token(q["link"]), "pin": mb["pin"]}).status_code == 200
    assert world["eng"].post("/api/qr/issue", json={"user_id": mb["id"]}).status_code == 200


def test_qr_email_mailto_without_smtp_and_mock_smtp(m, world, monkeypatch):
    b, mb = world["chief_b"], world["mech_b"]
    r = b.post("/api/qr/email", json={"user_id": mb["id"]}).json()
    assert r["sent"] is False and r["mailto"].startswith("mailto:s@example.com?") and r["qr_png"]
    sent = []
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid"); monkeypatch.setenv("SMTP_FROM", "bot@example.com")
    monkeypatch.setattr(m, "send_mail", lambda to, subj, text, png: sent.append((to, subj, "/q/" in text, png[:4])))
    r = b.post("/api/qr/email", json={"user_id": mb["id"]}).json()
    assert r["sent"] is True and sent == [("s@example.com", "Вход в «Цифровой механик»", True, b"\x89PNG")]
