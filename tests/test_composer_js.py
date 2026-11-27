"""Composer like ChatGPT: empty field shows dictation (mic) + voice chat (waveform);
any non-whitespace text replaces voice chat with Send (up arrow); mic stays; Enter sends."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "src/digital_mechanic/web/static/index.html").read_text(encoding="utf-8")
JS = (ROOT / "src/digital_mechanic/web/static/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "src/digital_mechanic/web/static/app.css").read_text(encoding="utf-8")
I18N = json.loads((ROOT / "src/digital_mechanic/web/static/i18n.json").read_text(encoding="utf-8"))


def test_empty_and_typed_buttons_in_markup():
    assert 'id="dictate"' in HTML and 'data-t-aria="dictate"' in HTML and 'data-icon="mic"' in HTML
    assert 'id="mic"' in HTML and 'data-t-aria="voice_chat"' in HTML and 'data-icon="audio-lines"' in HTML
    assert 'id="send"' in HTML and 'data-t-aria="send"' in HTML and 'data-icon="arrow-up"' in HTML
    # voice chat is the filled button on the right; dictation sits to its left (and stays when Send appears)
    assert HTML.index('id="dictate"') < HTML.index('id="send"') < HTML.index('id="mic"')
    assert "send-btn hidden" in HTML
    assert 'class="icon-btn voice-btn" id="mic"' in HTML


def test_autosize_swaps_voice_chat_for_send_keeps_dictate():
    fn = JS[JS.index("function autosize()"):JS.index("function layout()")]
    assert '$("#send").classList.toggle("hidden", !has)' in fn
    assert '$("#mic").classList.toggle("hidden", has)' in fn
    assert "dictate" not in fn or 'toggle("hidden"' not in fn.split("dictate")[-1][:80]
    # dictation must not be hidden when there is text
    assert '$("#dictate").classList.toggle("hidden"' not in JS


def test_handlers_reuse_existing_stt_and_voice_mode():
    assert '$("#dictate").onclick = dictateToggle' in JS
    assert '$("#mic").onclick = voiceOpen' in JS
    assert '$("#send").onclick = () => send()' in JS
    block = JS[JS.index("// ---------------------------------------------------------------- dictation into the composer"):JS.index("// ---------------------------------------------------------------- wiring")]
    assert "MediaRecorder" in block and "toWav" in block and '"/api/stt"' in block  # server dictation records real audio first
    assert "WebSTT.listen" in block and "dictateBrowserStart" in block                # browser fallback remains
    assert "dictateCancel" in block and "dictFinish" in block
    assert "dictationUpload" in block and "DICT.blob" in block and "rotate-ccw" in block  # failed upload keeps local audio for retry
    assert "v_didnt_hear" in block and "same recording can be retried" in block  # empty recognition also keeps the local recording
    assert "cancelled while the browser was converting" in block  # cancel before upload: audio is never sent
    assert "Enter" in JS and "send()" in JS
    # old single teal mic-as-voice-mode-or-send swap is gone: mic no longer is the only right button
    assert "dictate-btn" in CSS and "voice-btn" in CSS and "send-btn" in CSS


def test_dictation_has_messenger_style_recording_controls():
    assert HTML.index('id="dictCancel"') < HTML.index('id="dictWave"') < HTML.index('id="dictTime"') < HTML.index('id="dictDone"')
    assert 'data-icon="x"' in HTML and 'data-icon="check"' in HTML
    assert '$("#dictCancel").onclick = dictateCancel' in JS and '$("#dictDone").onclick = dictFinish' in JS
    assert ".inputbox.recording .dictrec{display:flex}" in CSS
    assert ".inputbox.recording textarea" in CSS and "#dictate" in CSS and "#mic" in CSS
    assert ".dictrec.error .dictdone" in CSS
    cancel = JS[JS.index("function dictateCancel()"):JS.index("async function dictateToggle()")]
    assert "DICT.seq++" in cancel and "dictReleaseMic" in cancel and "WebSTT.abort" in cancel


def test_aria_labels_russian():
    assert I18N["ru"]["dictate"] == "Диктовка"
    assert I18N["ru"]["retry"] == "Повторить"
    assert I18N["ru"]["voice_chat"] == "Голосовой чат"
    assert I18N["ru"]["send"] == "Отправить"
    for lang, m in I18N.items():
        assert m.get("dictate") and m.get("voice_chat") and m.get("send") and m.get("retry"), lang


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_autosize_toggles_in_node():
    # stub DOM: empty -> send hidden, voice visible; type -> send shown, voice hidden; dictate always shown
    stub = r"""
    const hidden = new Set();
    const el = id => ({
      style: {height: "0"},
      value: state.value,
      scrollHeight: 48,
      classList: {toggle(name, on) { if (name === "hidden") { if (on) hidden.add(id); else hidden.delete(id); } }},
    });
    const state = {value: ""};
    const $ = sel => ({"#txt": el("txt"), "#send": el("send"), "#mic": el("mic"), "#dictate": el("dictate"), "#composer": {classList:{contains:()=>false}, offsetHeight: 80}}[sel]);
    function layout() {}
    """
    fn = JS[JS.index("function autosize()"):JS.index("function layout()")]
    run = stub + fn + r"""
    state.value = ""; autosize();
    const empty = ["send:"+hidden.has("send"), "mic:"+hidden.has("mic"), "dictate:"+hidden.has("dictate")].join(",");
    state.value = "  момент  "; autosize();
    const typed = ["send:"+hidden.has("send"), "mic:"+hidden.has("mic"), "dictate:"+hidden.has("dictate")].join(",");
    state.value = "   "; autosize();
    const cleared = ["send:"+hidden.has("send"), "mic:"+hidden.has("mic"), "dictate:"+hidden.has("dictate")].join(",");
    process.stdout.write([empty, typed, cleared].join("|"));
    """
    out = subprocess.run(["node", "-e", run], capture_output=True, text=True, check=True, timeout=10).stdout
    assert out == "send:true,mic:false,dictate:false|send:false,mic:true,dictate:false|send:true,mic:false,dictate:false", out
