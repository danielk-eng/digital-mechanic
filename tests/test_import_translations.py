import gzip, json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_translations as it  # noqa: E402


def rec(t):
    return {"id": "x", "type": "repair_step", "text_ru": t}


def test_checks():
    r = rec("Затянуть болты ГБЦ моментом 196–216 Н·м, с. 31 [S1], код F08.")
    assert it.check(r, "uz", "GBTs boltlarini 196–216 N·m moment bilan torting, 31-bet [S1], kod F08.") is None
    assert "потеряны числа" in it.check(r, "uz", "GBTs boltlarini 196 N·m bilan torting, 31-bet [S1], kod F08.")
    assert "лишние" in it.check(r, "uz", "GBTs boltlarini 196–216 N·m, 31-bet [S1], kod F08, 5 marta.")
    assert "коды" in it.check(rec("Код F08, деталь 236-1003012"), "uz", "Kod F 08, detal 236-1003012")
    assert "письменности" in it.check(rec("Проверить зазор клапанов"), "hi", "Check valve clearance")
    assert it.check(rec("Проверить зазор клапанов"), "hi", "वाल्व क्लीयरेंस जांचें") is None
    assert "чужой" in it.check(rec("Проверить зазор клапанов двигателя на холодную"), "uz", "Klapan tirqishini sovuq dvigatelda tekshiring, проверить зазор клапанов")


def test_import_end_to_end(tmp_path):
    src, app = tmp_path / "перевод", tmp_path / "app"
    (src / "data").mkdir(parents=True); (app / "src/digital_mechanic/web/static").mkdir(parents=True)
    (app / "src/digital_mechanic/web/static/i18n.json").write_text(json.dumps({"ru": {"hello": "Привет"}, "uz": {}}), encoding="utf-8")
    rows = [{"id": "page:YAMZ236-RE:31", "type": "manual_page", "doc_id": "YAMZ236-RE", "page": 31, "text_ru": "Зазор 0,25–0,30 мм", "text_uz": "Tirqish 0,25–0,30 mm"},
            {"id": "ui:hello", "type": "ui_string", "text_ru": "Привет", "text_uz": "Salom"},
            {"id": "s1", "type": "repair_step", "text_ru": "Затянуть 118–147 Н·м", "text_uz": "118 N·m torting"}]
    (src / "data/a.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    (src / "glossary_ru.csv").write_text("ru,category,uz,tg,kk,hi,vi,bn,ky,be,status,note\nболт,деталь,bolt,,,,,,,,черновик,\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(Path(it.__file__)), str(src), "--app", str(app), "--langs", "uz"], capture_output=True, text=True)
    assert p.returncode == 1  # one record rejected
    with gzip.open(app / "data/i18n/pages_uz.jsonl.gz", "rt", encoding="utf-8") as fh:
        assert json.loads(fh.readline()) == {"doc_id": "YAMZ236-RE", "page": 31, "text": "Tirqish 0,25–0,30 mm"}
    assert json.loads((app / "src/digital_mechanic/web/static/i18n.json").read_text(encoding="utf-8"))["uz"]["hello"] == "Salom"
    assert json.loads((app / "data/i18n/glossary.json").read_text(encoding="utf-8"))["uz"]["болт"] == "bolt"
    assert "потеряны числа" in (src / "import_report.md").read_text(encoding="utf-8")
