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
