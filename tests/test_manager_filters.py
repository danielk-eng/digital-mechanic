"""Chief's filters: /api/fleet carries brand and assigned mechanics; the filter logic in manager.js (run in node);
no demo hints in the input placeholders."""
import importlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static"


@pytest.fixture(scope="module")
def app_mod(tmp_path_factory):
    d = tmp_path_factory.mktemp("mf")
    os.environ.update(DB_PATH=str(d / "a.db"), LOG_PATH=str(d / "l.jsonl"), UPLOADS_DIR=str(d / "up"), LLM_API_KEY="", LLM_API_KEY_FILE="", EMBED_MOCKS="1")
    import digital_mechanic.web.app as m
    importlib.reload(m)
    yield m
    os.environ.pop("EMBED_MOCKS", None)


def test_fleet_rows_have_make_and_mechanics(app_mod):
    c = TestClient(app_mod.app)
    assert c.post("/api/login/demo", json={"account": "chief", "pin": "1001", "consent": True}).status_code == 200
    vs = {v["vehicle_id"]: v for v in c.get("/api/fleet").json()["vehicles"]}
    assert all(v["make"] for v in vs.values())
    assert "Иванов А." in vs["O710HH78"]["mechanics"]  # demo veteran's truck
    assert {v["make"] for v in vs.values()} >= {"МАЗ", "ПАЗ"}


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_filter_logic_in_node():
    src = (STATIC / "manager.js").read_text()
    seg = src[src.index("// ---- Filters"):src.index("const vById")]
    js = seg + r"""
const V = [
 {vehicle_id: "A001MC178", garage_no: "Г-143", make: "УАЗ", model: "УАЗ Патриот", status: "quarantine", urgency: "high", in_repair: false, ops: "idle",
  mechanics: ["Иванов А."], last: {status: "anomaly", anomaly_type: "F03", checked_at: "2026-11-22T10:00:00"}},
 {vehicle_id: "O819CY198", garage_no: "Г-7", make: "ПАЗ", model: "ПАЗ-3205", status: "ready", urgency: null, in_repair: true, ops: "in_repair",
  mechanics: [], last: {status: "normal", checked_at: "2026-11-10T09:00:00"}},
];
const ids = f => { F = f; return V.filter(vMatch).map(v => v.vehicle_id).join(","); };
const out = {
 all: ids({}), plateCyr: ids({q: "а001 мс"}), garage: ids({q: "г-7"}), status: ids({status: "in_repair"}), make: ids({make: "ПАЗ"}),
 mech: ids({mech: "Иванов А."}), unassigned: ids({mech: "-"}), urg: ids({urg: "high"}), nourg: ids({urg: "-"}), code: ids({code: "F03"}),
 nocode: ids({code: "-"}), from: ids({from: "2026-11-15"}), to: ids({to: "2026-11-15"}), combo: ids({make: "УАЗ", code: "F05"}), ops: ids({ops: "op_idle"}),
};
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", "const t = k => k, esc = s => s, $ = () => null;\n" + js], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["all"] == "A001MC178,O819CY198" and o["plateCyr"] == "A001MC178" and o["garage"] == "O819CY198"
    assert o["status"] == o["make"] == o["unassigned"] == o["nourg"] == o["nocode"] == o["to"] == "O819CY198"
    assert o["mech"] == o["urg"] == o["code"] == o["from"] == o["ops"] == "A001MC178" and o["combo"] == ""


def test_url_keys_and_no_demo_placeholders():
    js = (STATIC / "manager.js").read_text()
    assert "history.replaceState" in js and "URLSearchParams" in js
    html = (STATIC / "manager.html").read_text() + (STATIC / "engineer.html").read_text() + (STATIC / "index.html").read_text()
    assert "последнюю машину" not in html and 'placeholder="Задайте вопрос"' in html
    ru = json.loads((STATIC / "i18n.json").read_text())["ru"]
    assert ru["ask_placeholder"] == "Задайте вопрос"
    for k, v in ru.items():
        if k.endswith("_ph") or "placeholder" in k:
            assert not re.search(r"например|Покажи", v), k


def test_every_agent_input_has_a_mic():
    """Mic audit: each chat/agent textarea on every role's page sits next to a voice button."""
    import re
    from pathlib import Path
    st = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static"
    for page, ta in (("index.html", "txt"), ("manager.html", "mtxt"), ("engineer.html", "etxt")):
        html = (st / page).read_text(encoding="utf-8")
        box = re.search(r'<[^>]*class="inputbox"[^>]*>.*?</div>|<footer class="composer.*?</footer>', html, re.S)
        found = [m for m in re.finditer(r'<textarea id="(\w+)"', html)]
        assert any(m.group(1) == ta for m in found), page
        seg = html[html.index(f'id="{ta}"'):html.index(f'id="{ta}"') + 600]
        assert "mic-btn" in seg, page
    assert "QRLink.mount" in (st / "app.js").read_text(encoding="utf-8")
    assert "QRLink.mount" in (st / "manager.js").read_text(encoding="utf-8")
    assert "companies()" in (st / "engineer.js").read_text(encoding="utf-8")
