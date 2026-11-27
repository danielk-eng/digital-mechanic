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
    assert client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": False}).status_code == 400


def test_mechanic_cannot_read_audit(client):
    assert client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": True}).status_code == 200
    assert client.get("/api/me").json()["user"]["role"] == "mechanic"
    assert client.get("/api/audit").status_code == 403
    vs = client.get("/api/vehicles").json()["vehicles"]
    assert len(vs) == 50
    p = client.get("/api/page/YAMZ236-RK/41").json()
    assert "421-461" in p["text"] and p["document"] == "236.01.01 РК"


def test_ml_down_is_reported_not_crash(client):
    r = client.post("/api/diagnose", json={"vehicle_id": "M333YK47", "session_id": "S-0005"})
    assert r.status_code == 502


def test_manager_sees_fleet_and_audit(client):
    client.post("/api/login/demo", json={"account": "chief", "pin": "1001", "consent": True})
    f = client.get("/api/fleet").json()
    assert f["counts"]["total"] == 50
    assert client.get("/api/audit").status_code == 200
    assert client.get("/api/audit.csv").status_code == 200


def test_delete_my_data(client):
    client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": True})
    assert client.delete("/api/me").json()["deleted"]
    assert client.get("/api/me").status_code == 401


def test_answer_language_separate_from_ui(client):
    import digital_mechanic.web.app as m
    from digital_mechanic.i18n import answer_lang
    client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": True, "lang": "ru"})
    u = client.post("/api/me", json={"answer_lang": "uz"}).json()["user"]
    assert u["lang"] == "ru" and answer_lang(u) == "uz"
    assert "узбекском" in m.AGENT._system(None, u, manager=True)
    u = client.post("/api/me", json={"answer_lang": "xx"}).json()["user"]
    assert answer_lang(u) == "ru"  # unknown -> same as interface


def test_fleet_ops_tiles(client):
    client.post("/api/login/demo", json={"account": "chief", "pin": "1001", "consent": True})
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
    assert r.json()["server_tts"] in (True, False)  # the browser skips POST /api/tts when false (was a 404 in the console)
    assert r.json()["server_stt"] in (True, False)  # false -> the browser recognises speech (Web Speech API), no 503 from /api/stt


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
    client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": True, "lang": "ru"})
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


def test_session_restored_from_signed_cookie_on_another_instance():
    """Vercel: each serverless instance has its own /tmp DB; the signed profile cookie restores the user."""
    from digital_mechanic.web import app as web
    u = web.STORE.upsert_user("u-restore-1", name="Иван", role="mechanic", lang="uz", experience="novice", consent_at=1.0)
    up = web.profile_cookie(u)
    web.STORE.x("DELETE FROM users WHERE id=?", ("u-restore-1",))
    r = web.restore_from_cookie("u-restore-1", up)
    assert r and r["lang"] == "uz" and r["experience"] == "novice"
    assert web.restore_from_cookie("u-other", up) is None  # bound to its uid
    assert web.restore_from_cookie("u-restore-1", up[:-2] + "00") is None  # tampered


def test_country_consent_and_privacy_page(client):
    from digital_mechanic import privacy_laws
    cs = client.get("/api/countries?lang=uz").json()
    assert cs["default"] == "RU" and {c["code"] for c in cs["countries"]} == {"RU", "UZ", "KZ", "KG", "BY", "TJ", "IN", "VN"}
    assert next(c for c in cs["countries"] if c["code"] == "UZ")["name"] == "Oʻzbekiston"
    r = client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": False, "country": "KZ"})
    assert r.status_code == 400 and "94-V" in r.json()["detail"]
    u = client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": True, "country": "UZ", "lang": "uz"}).json()["user"]
    assert u["country"] == "UZ"
    page = client.get("/privacy").text
    assert "ЗРУ-547" in page and "{{" not in page and "локально" in page
    assert "91/2025/QH15" in client.get("/privacy?country=VN").text and "152-ФЗ" in client.get("/privacy?country=RU").text
    assert client.post("/api/me", json={"country": "IN"}).json()["user"]["country"] == "IN"
    assert privacy_laws.country("xx") == "RU" and not privacy_laws.requires_localization("TJ")


def test_chat_stream_ndjson(client):
    """«Подробнее» on Vercel: /api/chat/stream gives step lines then the same turn as /api/chat; 401 before the stream."""
    import json as _j
    from fastapi.testclient import TestClient
    import digital_mechanic.web.app as m
    assert TestClient(m.app).post("/api/chat/stream", json={"text": "x"}).status_code == 401
    client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": True, "lang": "ru"})
    r = client.post("/api/chat/stream", json={"vehicle_id": "M333YK47", "text": "что значит F08?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    lines = [_j.loads(x) for x in r.text.splitlines() if x.strip()]
    assert lines[0].get("step") and "turn" in lines[-1] and lines[-1]["turn"].get("text")
    bad = client.post("/api/chat/stream", json={"vehicle_id": "NOPE000", "text": "x"})
    assert _j.loads(bad.text.splitlines()[-1]) == {"error": "unknown vehicle", "status": 404}


def test_pdf_link_without_pdfs_is_a_printable_section_not_404(client, monkeypatch, tmp_path):
    """Vercel backup has no manual PDFs: «Открыть PDF» used to 404; now a printable page of the cited section (same text as the app)."""
    import digital_mechanic.web.app as m
    monkeypatch.setattr(m.S, "manuals_dir", str(tmp_path / "none"))
    client.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": True})
    s = client.get("/api/section/YAMZ236-RK/41").json()
    assert s["pdf"] is False and s["pdf_text"] is True
    r = client.get("/manuals/YAMZ236-RK.pdf?page=41")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "421-461" in r.text and "Сохранить в PDF" in r.text and "Страница 41" in r.text
    assert client.get("/manuals/YAMZ236-RK.pdf?page=99999").status_code == 404
    assert client.get("/manuals/YAMZ236-RK.pdf").status_code == 200  # old link without ?page=: start of the manual, not 404
