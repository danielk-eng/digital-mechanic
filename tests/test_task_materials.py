"""Task materials loader (organizers 27.11): stubs and vehicle cards are not in git, the stand downloads them from TASK_MATERIALS_URL."""
import importlib.util, io, json, subprocess, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("ftm", ROOT / "scripts/fetch_task_materials.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_overlay_fills_maz_model_only_when_null():
    m = _load()
    cards = {"vehicles": [
        {"vehicle_id": "A", "make": "МАЗ", "engine_model": "ЯМЗ-536", "model": None},
        {"vehicle_id": "B", "make": "МАЗ", "engine_model": "ЯМЗ-536", "model": "МАЗ-6312"},
        {"vehicle_id": "C", "make": "ПАЗ", "engine_model": "ЗМЗ-5234", "model": None}]}
    assert m.apply_overlay(cards) == 1
    assert [v["model"] for v in cards["vehicles"]] == ["МАЗ-5340С2", "МАЗ-6312", None]


def test_install_from_archive(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/vehicle_overlay.json").write_text((ROOT / "data/vehicle_overlay.json").read_text(encoding="utf-8"), encoding="utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mock_x/data/vehicles.json", json.dumps({"vehicles": [{"vehicle_id": "A", "make": "МАЗ", "engine_model": "ЯМЗ-536", "model": None}]}))
        z.writestr("mock_x/data/fault_types.json", "[]")
        z.writestr("mock_x/mock_ml/app.py", "app = None\n")
        z.writestr("mock_x/Dockerfile", "FROM scratch\n")
    m.install_stubs(buf.getvalue())
    assert (tmp_path / "vendor/neurodvs_mock/mock_ml/app.py").exists()
    assert not (tmp_path / "vendor/neurodvs_mock/Dockerfile").exists()  # ours stays, never overwritten
    v = json.loads((tmp_path / "data/official/vehicles.json").read_text(encoding="utf-8"))
    assert v["vehicles"][0]["model"] == "МАЗ-5340С2"


def test_materials_not_tracked():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    bad = [f for f in files if f.endswith(".pdf") or f.startswith("data/official/")
           or (f.startswith("vendor/neurodvs_mock/") and f != "vendor/neurodvs_mock/Dockerfile")]
    assert bad == []
    assert "disk.yandex" not in (ROOT / "data/manuals_manifest.json").read_text(encoding="utf-8")  # the link is env-only
