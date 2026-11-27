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


def _md(text: str, sources: list) -> str:
    src = JS.read_text(encoding="utf-8")
    fns = src[src.index("function inline("):src.index("const ACT_ICON")]
    pre = ("const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');"
           "const t=k=>({page_short:'с.'})[k]||k;")
    out = subprocess.run(["node", "-e", pre + fns + f"; process.stdout.write(md({json.dumps(text, ensure_ascii=False)}, {json.dumps(sources)}))"],
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_same_page_chip_once_per_paragraph_and_list():
    """Audit 23:17 (390 px): «с. 52» after each of five lines of one answer. One chip per run of the same page in a
    paragraph or list; another page in between keeps both."""
    S = [{"label": "S1", "doc_id": "D", "page": 52, "document": "РЭ"}, {"label": "S2", "doc_id": "D", "page": 53, "document": "РЭ"}]
    h = _md("F08 — четыре причины [S1].\nПервая: воздух [S1].\nВторая: насос [S1].\nТретья: рейка [S2].", S)
    assert h.count('data-page="52"') == 1 and h.count('data-page="53"') == 1
    assert h.index('data-page="52"') > h.index("Вторая")  # the chip stays after the last line of the run
    h = _md("**Что проверить**\n1. Герметичность [S1].\n2. Насос [S1].\n3. Рейка [S1].", S)
    assert h.count('data-page="52"') == 1 and h.index('data-page="52"') > h.index("Рейка")
    h = _md("Первая [S1].\n\nВторая [S1].", S)  # separate paragraphs keep their own chip
    assert h.count('data-page="52"') == 2
