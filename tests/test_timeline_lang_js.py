"""History in the interface language: month names per language (were «19 ноя 2026» in every language) and the
«last check … days ago» / «recurring» line through the page's translator (run in node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/timeline.js"


def _run(lang: str, expr: str) -> str:
    pre = f"globalThis.ME = {{lang: {json.dumps(lang)}}}; globalThis.t = k => ({{last_check: 'Oxirgi tekshiruv', days_ago: 'kun oldin'}})[k] || k;"
    out = subprocess.run(["node", "-e", pre + JS.read_text(encoding="utf-8") + f"; process.stdout.write(String({expr}))"],
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_month_names_follow_ui_language():
    assert _run("ru", 'fmtDate("2026-05-02")') == "2 мая 2026"
    assert _run("uz", 'fmtDate("2026-11-19")') == "19 noy 2026"
    assert _run("kk", 'fmtDate("2026-10-29T15:20", true)') == "29 қаз 2026, 15:20"
    assert _run("vi", 'fmtDate("2026-11-19")') == "19 thg 11, 2026"
    assert _run("xx", 'fmtDate("2026-11-19")') == "19 ноя 2026"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_summary_line_translated():
    h = _run("uz", 'renderTimeline({days_since_last_check: 8, last_check: "2026-11-19", recurring: [], trend: [], entries: [], media: []})')
    assert "Oxirgi tekshiruv 19 noy 2026, 8 kun oldin" in h and "Последняя" not in h
