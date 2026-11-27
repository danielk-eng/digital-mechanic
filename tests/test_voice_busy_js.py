"""Vercel audit 27.11 10:50: the hands-free dialog said «Ответа нет» 1 s after the phrase — ask() drops a request while another
answer is in flight (BUSY). The voice turn waits for it (bounded) and only then asks (run in node with stubs)."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_voice_waits_while_busy():
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("async function vSendHeard"):src.index("function voiceClose")]
    stub = """
let BUSY = true; const VOICE = {on: true}; const log = [];
const el = {innerHTML: "", textContent: "", classList: {remove() {}, toggle() {}}, scrollTop: 0};
const $ = () => el; const esc = s => s; const rawDetails = () => ""; const t = k => k; const plain = s => s; const clipText = s => s;
const A11Y = {voiceSpeaks: () => false}; const vListen = () => log.push("listen"); const Player = {play() {}};
function vSet(s, h) { log.push("state:" + s + (h ? ":" + h : "")); }
async function send(text) { log.push(BUSY ? "send-while-busy" : "send"); return BUSY ? null : {text: "ответ"}; }
setTimeout(() => { BUSY = false; }, 900);
"""
    run = stub + fn + "; vSendHeard('почему стучит', null).then(() => process.stdout.write(log.join('|')));"
    out = subprocess.run(["node", "-e", run], capture_output=True, text=True, check=True, timeout=20).stdout
    assert out == "state:thinking|send|listen", out
