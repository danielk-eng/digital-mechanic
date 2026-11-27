"""Voice screen: a long spoken answer is shown as whole sentences (was cut mid-word «двигатель переби…», audit 27.11 01:20,
390 px); «стр. 53» is not a sentence end. app.js clipText() run in node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"


def _clip(text: str, n: int) -> str:
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("function clipText"):src.index("async function vHeard")]
    out = subprocess.run(["node", "-e", fn + f"; process.stdout.write(clipText({json.dumps(text, ensure_ascii=False)}, {n}))"],
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_voice_reply_clipped_at_sentence_end_not_at_page_abbreviation():
    t = ("По книге, стр. 53: стук на холостом может быть от раннего впрыска топлива. Ещё причина — сбитая регулировка "
         "клапанного механизма: проверь. И третье со стр. 53 — пониженное давление масла и так далее двигатель перебирать")
    assert _clip(t, 160) == "По книге, стр. 53: стук на холостом может быть от раннего впрыска топлива. Ещё причина — сбитая регулировка клапанного механизма: проверь. …"
    assert _clip("коротко", 100) == "коротко"
    one = "слово " * 40
    c = _clip(one, 50)
    assert c.endswith("слово…") and len(c) <= 51  # no sentence end: whole words only
