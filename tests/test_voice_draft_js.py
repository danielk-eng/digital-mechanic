"""Audit 27.11 12:20 (Vercel, 390 px): the hands-free screen showed one static «Ищу ответ в руководстве» for ~21 s. The lines
streamed so far now show dimmed under the phrase while the voice state is «thinking», never over a spoken answer (node)."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_draft_lines_show_only_while_thinking():
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("function vDraft"):src.index("async function vSendHeard")]
    stub = """
const cls = new Set(); const el = {textContent: "", classList: {add: c => cls.add(c), remove: c => cls.delete(c), toggle: (c, on) => on ? cls.add(c) : cls.delete(c)}};
const $ = () => el; const plain = s => s.replace(/\\[S\\d+\\]/g, "").trim(); const clipText = (s, n) => s.slice(0, n);
const VOICE = {on: true, state: "thinking"};
"""
    run = stub + fn + """
vDraft("Сначала проверь зазоры клапанов [S1]");
const a = el.textContent + "|" + [...cls].join(",");
VOICE.state = "speaking"; vDraft("другое");
const b = el.textContent;
VOICE.state = "thinking"; VOICE.on = false; vDraft("ещё");
process.stdout.write([a, b, el.textContent].join("#"));"""
    out = subprocess.run(["node", "-e", run], capture_output=True, text=True, check=True, timeout=20).stdout
    a, b, c = out.split("#")
    assert a.startswith("Сначала проверь зазоры клапанов|") and "draft" in a and "[S1]" not in a
    assert b == c == "Сначала проверь зазоры клапанов"
