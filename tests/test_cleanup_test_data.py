"""Engineer removes load-test leftovers from the chief's dashboard: race-marker handoffs and duplicate ticket numbers."""
import importlib
import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    d = tmp_path_factory.mktemp("clean")
    os.environ.update(DB_PATH=str(d / "a.db"), LOG_PATH=str(d / "l.jsonl"), UPLOADS_DIR=str(d / "up"), LLM_API_KEY="", LLM_API_KEY_FILE="", EMBED_MOCKS="1")
    import digital_mechanic.web.app as mod
    importlib.reload(mod)
    yield mod
    os.environ.pop("EMBED_MOCKS", None)


def login(m, key, pin):
    c = TestClient(m.app)
    assert c.post("/api/login/demo", json={"account": key, "pin": pin, "consent": True}).status_code == 200
    return c


def test_cleanup_removes_race_handoffs_and_duplicate_tickets_only(m):
    S, now = m.STORE, time.time()
    for i, note in enumerate(["race-222019-0", "race-222019-1", "Прошу посмотреть стук", "race в тексте не маркер"]):
        S.x("INSERT INTO handoffs(ts, user_id, vehicle_id, kind, note, summary, payload) VALUES (?,?,?,?,?,?,?)",
            (now + i, "demo-sidorov", "M333YK47", "handoff", note, "", "{}"))
    for i, tid in enumerate(["TKT-0001", "TKT-0002", "TKT-0001", "TKT-0002", "TKT-0003"]):
        S.x("INSERT INTO tickets(ts, user_id, vehicle_id, ticket_id, payload) VALUES (?,?,?,?,?)", (now + i, "demo-sidorov", "M333YK47", tid, "{}"))
    assert login(m, "chief", "1001").post("/api/eng/cleanup-test-data").status_code in (401, 403)  # engineer only
    r = login(m, "engineer", "2002").post("/api/eng/cleanup-test-data")
    assert r.status_code == 200 and r.json() == {"handoffs_deleted": 2, "tickets_deleted": 2}
    assert sorted(h["note"] for h in S.q("SELECT note FROM handoffs")) == ["race в тексте не маркер", "Прошу посмотреть стук"]
    left = S.q("SELECT ticket_id, ts FROM tickets ORDER BY ticket_id")
    assert [t["ticket_id"] for t in left] == ["TKT-0001", "TKT-0002", "TKT-0003"] and left[0]["ts"] == now + 2  # the newest copy kept


def test_cleanup_audit_run_removes_only_that_account_since_start(m):
    S, now = m.STORE, time.time() + 1000
    S.x("DELETE FROM tickets"); S.x("DELETE FROM handoffs")
    rows = [("demo-sidorov", "M333YK47", now - 50, "TKT-0100"), ("demo-sidorov", "M333YK47", now + 1, "TKT-0101"),
            ("demo-sidorov", "M333YK47", now + 2, "TKT-0102"), ("demo-ivanov", "M333YK47", now + 3, "TKT-0103"), ("demo-sidorov", "P456AT178", now + 4, "TKT-0104")]
    for u, v, ts, tid in rows:
        S.x("INSERT INTO tickets(ts, user_id, vehicle_id, ticket_id, payload) VALUES (?,?,?,?,?)", (ts, u, v, tid, "{}"))
        S.x("INSERT INTO handoffs(ts, user_id, vehicle_id, kind, note, summary, payload) VALUES (?,?,?,?,?,?,?)", (ts, u, v, "handoff", "Прошу посмотреть сегодня", "", "{}"))
    eng = login(m, "engineer", "2002")
    assert eng.post("/api/eng/cleanup-test-data", json={"since": now}).status_code == 400  # the account is required
    r = eng.post("/api/eng/cleanup-test-data", json={"since": now, "user_id": "demo-sidorov", "vehicle_id": "M333YK47", "keep_ticket_ids": ["TKT-0102"]})
    assert r.status_code == 200 and r.json()["audit_tickets_deleted"] == 1 and r.json()["audit_handoffs_deleted"] == 2
    assert [t["ticket_id"] for t in S.q("SELECT ticket_id FROM tickets ORDER BY ticket_id")] == ["TKT-0100", "TKT-0102", "TKT-0103", "TKT-0104"]
    assert len(S.q("SELECT id FROM handoffs")) == 3
    assert login(m, "chief", "1001").post("/api/eng/cleanup-test-data", json={"since": 0, "user_id": "demo-sidorov"}).status_code in (401, 403)
