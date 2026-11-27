"""«Подробнее» from a single manual page showed «с. 52» after each of ~10 paragraphs: when every chip of an answer is the
same page only the first and the last stay; answers that cite several pages keep all their chips (run in node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"
CHIP = '<button class="cite" data-doc="d1" data-page="{p}" data-q="x">с. {p}</button>'


def _run(h: str) -> str:
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("function onePageCites"):src.index("function collapseCites")]
    return subprocess.run(["node", "-e", fn + f"; process.stdout.write(onePageCites({json.dumps(h, ensure_ascii=False)}))"],
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_single_page_answer_keeps_first_and_last_chip():
    h = "".join(f"<p>Шаг {i}. {CHIP.format(p=52)}</p>" for i in range(6))
    out = _run(h)
    assert out.count('data-page="52"') == 2 and out.startswith("<p>Шаг 0. <button") and out.endswith("</button></p>")
    assert out.count("<p>") == 6


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_several_pages_untouched():
    h = f"<p>a {CHIP.format(p=52)}</p><p>b {CHIP.format(p=53)}</p><p>c {CHIP.format(p=52)}</p>"
    assert _run(h) == h
    two = f"<p>a {CHIP.format(p=52)}</p><p>b {CHIP.format(p=52)}</p>"
    assert _run(two) == two
