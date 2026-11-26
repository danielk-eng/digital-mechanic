"""Roles, demo accounts, user management, onboarding, manager briefing with insights, persona styles, memory callbacks."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app_mod(tmp_path_factory):
    d = tmp_path_factory.mktemp("roles")
    os.environ.update(DB_PATH=str(d / "a.db"), LOG_PATH=str(d / "l.jsonl"), UPLOADS_DIR=str(d / "up"), LLM_API_KEY="", LLM_API_KEY_FILE="", EMBED_MOCKS="1")
    for k in ("ML_SERVICE_URL", "TOIR_SERVICE_URL"):
        os.environ.pop(k, None)
    import digital_mechanic.web.app as m
    importlib.reload(m)
    yield m
    os.environ.pop("EMBED_MOCKS", None)


def login(m, key, pin):
    c = TestClient(m.app)
    r = c.post("/api/login/demo", json={"account": key, "pin": pin, "consent": True})
    assert r.status_code == 200, r.text
    return c


def test_demo_accounts_and_wrong_pin(app_mod):
    c = TestClient(app_mod.app)
    acc = {a["key"]: a for a in c.get("/api/demo/accounts").json()["accounts"]}
    assert {"chief", "engineer", "veteran", "selftaught", "student"} <= set(acc)
    assert acc["chief"]["role"] == "manager" and acc["engineer"]["role"] == "engineer"
    assert c.post("/api/login/demo", json={"account": "chief", "pin": "0000", "consent": True}).status_code == 403


def test_rbac_matrix(app_mod):
    chief, eng, mech = login(app_mod, "chief", "1001"), login(app_mod, "engineer", "2002"), login(app_mod, "veteran", "3001")
    assert chief.get("/api/fleet").status_code == 200 and chief.get("/api/admin/users").status_code == 200
    assert chief.get("/api/eng/logs").status_code == 403
    assert eng.get("/api/eng/logs").status_code == 200 and eng.get("/api/eng/health").status_code == 200 and eng.get("/api/eng/models").status_code == 200
    assert eng.get("/api/fleet").status_code == 403 and eng.post("/api/admin/users", json={"name": "X"}).status_code == 403
    for p in ("/api/fleet", "/api/eng/logs", "/api/admin/users", "/api/eng/mcp"):
        assert mech.get(p).status_code == 403, p
    assert mech.post("/api/admin/users", json={"name": "X"}).status_code == 403
    # free login cannot become an engineer
    free = TestClient(app_mod.app)
    for role in ("engineer", "manager", "mechanic"):  # name-only login is gone for every role
        assert free.post("/api/login", json={"name": "Хакер", "role": role, "consent": True}).status_code == 410
    assert free.get("/api/eng/logs").status_code == 401 and free.get("/api/fleet").status_code == 401
    assert "model" not in free.get("/api/health").json()  # the model name is only in the engineer menu


def test_engineer_knobs_and_mcp_validation(app_mod):
    eng = login(app_mod, "engineer", "2002")
    assert eng.post("/api/eng/models", json={"values": {"stt_order": "nonsense"}}).status_code == 400
    r = eng.post("/api/eng/models", json={"values": {"llm_timeout_s": "40"}}).json()
    assert r["current"]["llm_timeout_s"] == 40.0
    assert eng.post("/api/eng/mcp", json={"name": "x", "url": "file:///etc/passwd"}).status_code == 400
    assert eng.post("/api/eng/mcp", json={"name": "x", "url": "https://example.org/mcp", "token_env": "sk-123"}).status_code == 400
    assert eng.post("/api/eng/mcp", json={"name": "toir", "url": "https://example.org/mcp", "token_env": "TOIR_TOKEN"}).json()["servers"][0]["name"] == "toir"


def test_chief_creates_user_onboarding_and_deactivation(app_mod):
    chief = login(app_mod, "chief", "1001")
    r = chief.post("/api/admin/users", json={"name": "Орлов Н.", "role": "mechanic", "experience": "novice", "vehicles": ["M333YK47"], "title": "Слесарь"}).json()
    assert r["pin"].isdigit() and r["user"]["onboarded"] is False
    new = login(app_mod, r["login"], r["pin"])
    o = new.get("/api/onboarding").json()
    assert o["needed"] and o["sample"]["formal"] != o["sample"]["informal"]
    st = new.post("/api/onboarding", json={"formality": "formal", "detail": "detailed", "experience": "novice"}).json()["style"]
    assert "«вы»" in st["text"] and st["source"] == "onboarding" and st["v"] == 1
    assert new.get("/api/onboarding").json()["needed"] is False
    edited = new.post("/api/me/style", json={"text": "Коротко, на «вы», без шуток."}).json()["style"]
    assert edited["v"] == 2 and edited["source"] == "user"
    assert chief.post(f"/api/admin/users/{r['user']['id']}", json={"active": False}).json()["user"]["active"] is False
    assert new.get("/api/me").status_code == 401
    assert TestClient(app_mod.app).post("/api/login/demo", json={"account": r["login"], "pin": r["pin"], "consent": True}).status_code == 403
    card = chief.get("/api/admin/users/demo-sidorov").json()
    assert card["style"]["persona"] == "selftaught" and "P456AT178" in card["worked_on"] and card["history"]


def test_manager_briefing_last_scan_insights_and_assignment(app_mod):
    chief = login(app_mod, "chief", "1001")
    r = chief.post("/api/chat", json={"text": "Покажи машину, которую сканировали последней"}).json()
    card = next(c for c in r["cards"] if c["kind"] == "vehicle_brief")
    assert card["vehicle_id"] == "M851TA178" and card["anomaly_type"] == "F11" and card["scanned_by"] == "Кузнецов Д."
    kinds = {i["kind"]: i for i in card["insights"]}
    assert kinds["repeat"]["days"] == 14 and kinds["repeat"]["ticket_id"] == "TKT-DEMO-112"
    assert kinds["similar"]["vehicle_id"] == "T801PO178" and kinds["similar"]["result"] == "normal"
    assert {(s["doc_id"], s["page"]) for s in r["sources"]} >= {("ZMZ5234-RK", 50)}  # cited page of THIS engine's manual
    assert "Создать" in r["speak"] and r["actions"][0]["id"] == "brief_yes"
    done = chief.post("/api/chat", json={"text": "Да, создавай, назначь Иванова"}).json()
    tk = next(c for c in done["cards"] if c["kind"] == "ticket_created")
    assert tk["assignee"] == "Иванов А." and tk["request"]["references"]
    vet = login(app_mod, "veteran", "3001")
    a = vet.get("/api/my/assignments").json()["assignments"]
    assert a and a[0]["ticket_id"] == tk["ticket_id"] and a[0]["vehicle_id"] == "M851TA178"
    hs = chief.get("/api/handoffs").json()["handoffs"]  # sender shown by name to the chief, not «A***p»
    assert any(h["user_name"].startswith("Александр") and "***" not in h["user_name"] for h in hs)


def test_persona_styles_differ_and_memory_callback(app_mod):
    from digital_mechanic.agent import memory, style
    texts = {p: style.SEEDS[p] for p in ("veteran", "selftaught", "student", "manager", "engineer")}
    assert "«ты»" in texts["veteran"] and "40 лет" in texts["veteran"] and "моменты" in texts["selftaught"] and "По одному шагу" in texts["student"]
    assert "«вы»" in texts["manager"] and "«вы»" in texts["engineer"]
    m = app_mod
    stud = m.STORE.user("demo-kuznetsov")
    line, work = memory.hint(m.STORE, m.DEMO, stud, "M333YK47")
    assert work and "вчера" in line and "форсунк" in line
    assert memory.hint(m.STORE, m.DEMO, stud, "M333YK47", enabled=False) == ("", [])
    assert memory.hint(m.STORE, m.DEMO, stud, "P456AT178")[0] == ""  # only the truck in front of him
    assert memory.hint(m.STORE, m.DEMO, m.STORE.user("demo-chief"), "M333YK47")[0] == ""
