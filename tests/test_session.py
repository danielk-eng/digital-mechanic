"""PIN once: the signed session cookie survives reloads, a new browser process (same cookie jar), an app restart and a
fresh DB (serverless instance / redeploy); only logout ends it. A forged plain «uid» cookie gives nothing."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean_env():
    yield
    for k in ("EMBED_MOCKS", "SESSION_SECRET"):
        os.environ.pop(k, None)


def _app(tmp_path, name):
    os.environ.update(DB_PATH=str(tmp_path / name), LOG_PATH=str(tmp_path / "l.jsonl"), UPLOADS_DIR=str(tmp_path / "up"),
                      LLM_API_KEY="", LLM_API_KEY_FILE="", EMBED_MOCKS="1", SESSION_SECRET="test-secret")
    import digital_mechanic.web.app as m
    return importlib.reload(m)


def test_pin_once_until_logout(tmp_path):
    m = _app(tmp_path, "a.db")
    c = TestClient(m.app)
    r = c.post("/api/login/demo", json={"account": "chief", "pin": "1001", "consent": True})
    assert r.status_code == 200
    set_cookie = r.headers.get_list("set-cookie")
    s_hdr = next(h for h in set_cookie if h.startswith("s="))
    assert "HttpOnly" in s_hdr and "Max-Age=34560000" in s_hdr  # 400 days, the browser maximum
    jar = {"s": c.cookies.get("s"), "up": c.cookies.get("up")}
    for _ in range(3):  # reloads
        assert c.get("/api/session").json()["user"]["role"] == "manager"
    # browser restart: a new client with the same persisted cookies
    b = TestClient(m.app); [b.cookies.set(k, v) for k, v in jar.items()]
    assert b.get("/api/fleet").status_code == 200
    # redeploy / another serverless instance: fresh module and an empty DB, same secret
    m2 = _app(tmp_path, "b.db")
    d = TestClient(m2.app); [d.cookies.set(k, v) for k, v in jar.items()]
    assert d.get("/api/session").json()["user"]["role"] == "manager"
    assert d.get("/api/fleet").status_code == 200
    # logout ends it; the old cookie replayed on the same stand is refused
    assert d.post("/api/logout").status_code == 200
    assert d.get("/api/session").json()["user"] is None
    e = TestClient(m2.app); [e.cookies.set(k, v) for k, v in jar.items()]
    assert e.get("/api/session").json()["user"] is None and e.get("/api/fleet").status_code == 401


def test_forged_uid_cookie_is_ignored(tmp_path):
    m = _app(tmp_path, "c.db")
    c = TestClient(m.app)
    c.post("/api/login/demo", json={"account": "chief", "pin": "1001", "consent": True})
    uid = m.parse_session(c.cookies.get("s"))["u"]
    f = TestClient(m.app); f.cookies.set("uid", uid)
    assert f.get("/api/session").json()["user"] is None and f.get("/api/fleet").status_code == 401
    bad = c.cookies.get("s")[:-2] + "00"
    g = TestClient(m.app); g.cookies.set("s", bad)
    assert g.get("/api/fleet").status_code == 401
