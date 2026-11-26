import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("web")
    os.environ.update(DB_PATH=str(d / "a.db"), LOG_PATH=str(d / "l.jsonl"), UPLOADS_DIR=str(d / "up"), LLM_API_KEY="", LLM_API_KEY_FILE="",
                      ML_SERVICE_URL="http://127.0.0.1:9", TOIR_SERVICE_URL="http://127.0.0.1:9")
    import digital_mechanic.web.app as m
    importlib.reload(m)
    return TestClient(m.app)


def test_consent_required(client):
    assert client.post("/api/login", json={"name": "A", "consent": False}).status_code == 400


def test_mechanic_cannot_read_audit(client):
    assert client.post("/api/login", json={"name": "Петров П.П.", "role": "mechanic", "consent": True}).status_code == 200
    me = client.get("/api/me").json()["user"]
    assert "Петров" not in me["name"]  # PII masked
    assert client.get("/api/audit").status_code == 403
    vs = client.get("/api/vehicles").json()["vehicles"]
    assert len(vs) == 50
    p = client.get("/api/page/YAMZ236-RK/41").json()
    assert "421-461" in p["text"] and p["document"] == "236.01.01 РК"


def test_ml_down_is_reported_not_crash(client):
    r = client.post("/api/diagnose", json={"vehicle_id": "M333YK47", "session_id": "S-0005"})
    assert r.status_code == 502


def test_manager_sees_fleet_and_audit(client):
    client.post("/api/login", json={"name": "Главный", "role": "manager", "consent": True})
    f = client.get("/api/fleet").json()
    assert f["counts"]["total"] == 50
    assert client.get("/api/audit").status_code == 200
    assert client.get("/api/audit.csv").status_code == 200


def test_delete_my_data(client):
    client.post("/api/login", json={"name": "X", "consent": True})
    assert client.delete("/api/me").json()["deleted"]
    assert client.get("/api/me").status_code == 401


def test_answer_language_separate_from_ui(client):
    import digital_mechanic.web.app as m
    from digital_mechanic.i18n import answer_lang
    client.post("/api/login", json={"name": "L", "consent": True, "lang": "ru"})
    u = client.post("/api/me", json={"answer_lang": "uz"}).json()["user"]
    assert u["lang"] == "ru" and answer_lang(u) == "uz"
    assert "узбекском" in m.AGENT._system(None, u, manager=True)
    u = client.post("/api/me", json={"answer_lang": "xx"}).json()["user"]
    assert answer_lang(u) == "ru"  # unknown -> same as interface


def test_fleet_ops_tiles(client):
    client.post("/api/login", json={"name": "Главный", "role": "manager", "consent": True})
    f = client.get("/api/fleet").json()
    o = f["ops"]
    assert o["in_repair"] + o["on_line"] + o["idle"] == f["counts"]["total"]
    assert o["idle"] == sum(v["ops"] == "idle" for v in f["vehicles"]) and o["awaiting"] >= 0


def test_manager_dashboard_translated_to_all_languages():
    import json, re
    from pathlib import Path
    st = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static"
    d = json.loads((st / "i18n.json").read_text(encoding="utf-8"))
    keys = set(re.findall(r't\("([a-z_0-9]+)"', (st / "manager.js").read_text(encoding="utf-8"))) | \
        set(re.findall(r'data-t="([a-z_0-9]+)"', (st / "manager.html").read_text(encoding="utf-8")))
    keys = {k for k in keys if k in d["ru"]}
    for lang in d:
        assert not [k for k in keys if k not in d[lang]], lang


def test_session_endpoint_no_401():
    from fastapi.testclient import TestClient
    from digital_mechanic.web.app import app
    r = TestClient(app).get("/api/session")
    assert r.status_code == 200 and r.json()["user"] is None


def test_vehicle_title_not_overwritten_by_i18n():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/index.html").read_text(encoding="utf-8")
    assert 'id="vehTitle" data-t=' not in html  # applyI18n() used to reset the plate to «Выберите машину» after a settings change


def test_manual_pdf_served_inline():
    import inspect
    from digital_mechanic.web import app as web
    src = inspect.getsource(web.manual_pdf)
    assert 'content_disposition_type="inline"' in src


def test_text_gzip_but_not_pdf(client):
    r = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    from digital_mechanic.web.app import TextGZip
    assert ".pdf" in TextGZip.SKIP


def test_progress_has_live_draft(client):
    client.post("/api/login", json={"name": "T", "role": "mechanic", "lang": "ru", "consent": True})
    r = client.get("/api/progress?vehicle_id=M333YK47")
    assert r.status_code == 200 and "draft" in r.json() and "steps" in r.json()


def test_pwa_manifest_icons_exist():
    import json
    from pathlib import Path
    st = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static"
    m = json.loads((st / "manifest.json").read_text(encoding="utf-8"))
    assert {i["purpose"] for i in m["icons"] if i.get("purpose")} >= {"any", "maskable"}
    for i in m["icons"]:
        assert (st / i["src"].split("/static/")[1]).exists()
    assert "apple-touch-icon" in (st / "index.html").read_text(encoding="utf-8")
