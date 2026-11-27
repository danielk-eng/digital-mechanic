"""«Подробнее» came back from the free model as one line («… [S1]. **Что проверить** 1. … 2. … 3. …») and was drawn as a
wall of text: app.js unflatten() puts bold headings and a 1-2-3 run back on separate lines (run in node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"


def _unflatten(text: str) -> str:
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("function unflatten"):src.index("function md(")]
    out = subprocess.run(["node", "-e", fn + f"; process.stdout.write(unflatten({json.dumps(text, ensure_ascii=False)}))"],
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_flat_detailed_answer_gets_headings_and_list_lines():
    t = ("**Вероятная причина** — F08, стр. 52 [S1]. **Что проверить** 1. Герметичность — прокачать [S1]. "
         "2. Насос — разобрать [S1]. 3. Клапаны — промыть [S1]. **Как исправить** — по каждому исходу [S1].")
    assert _unflatten(t).split("\n") == [
        "**Вероятная причина** — F08, стр. 52 [S1].", "**Что проверить**", "1. Герметичность — прокачать [S1].",
        "2. Насос — разобрать [S1].", "3. Клапаны — промыть [S1].", "**Как исправить** — по каждому исходу [S1]."]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_text_without_a_real_list_is_untouched():
    for t in ("Момент 1. затяжки 2. не трогать", "Шаг 1. А [S1]. 2. Б", "1. А\n2. Б\n3. В"):
        assert _unflatten(t) == t


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_manager_idle_days_use_decimal_comma():
    src = (JS.parent / "manager.js").read_text(encoding="utf-8")
    line = next(l for l in src.split("\n") if l.startswith("const dec ="))
    out = subprocess.run(["node", "-e", "global.document={documentElement:{lang:'ru'}};" + line + "; process.stdout.write(dec(15.9))"],
                         capture_output=True, text=True, check=True).stdout
    assert out == "15,9" and "${dec(idleMax)}" in src and "${dec(v.idle_days)}" in src


def test_fleet_table_last_check_uses_short_date_and_decimal_comma():
    src = (JS.parent / "manager.js").read_text(encoding="utf-8")
    assert 'fmtDate((l.checked_at || "").slice(0, 10))' in src and "_dec(l.anomaly_score)" in src  # «20 ноя 2026, 0,93», was «2026-11-20, 0.93»
