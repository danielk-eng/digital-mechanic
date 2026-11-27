"""Tap sounds (a11y.js): WebAudio blip with peak gain <= 0.08, two-note mic cue, off switch persisted, silent while the mic is live."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/a11y.js"

PROG = r"""
const store = {}; const listeners = {}; const tones = []; let voiceHidden = true, recOn = false;
global.localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
global.performance = {t: 0, now() { return this.t; }};
class Param { constructor(n) { this.n = n; } setValueAtTime(v) { this.v = v; } exponentialRampToValueAtTime(v) { this.max = Math.max(this.max || 0, v); this.last = v; } }
class Osc { constructor() { this.frequency = new Param("f"); } connect() {} start(t) { this.t = t; } stop() {} }
class Gain { constructor() { this.gain = new Param("g"); } connect() {} }
global.window = {AudioContext: class { constructor() { this.state = "running"; this.currentTime = 1; this.destination = {}; }
  createOscillator() { const o = new Osc(); tones.push(o); return o; } createGain() { const g = new Gain(); gains.push(g); return g; } resume() { return Promise.resolve(); } }};
const gains = [];
const el = (id, cls = []) => ({id, disabled: false, classList: {contains: c => cls.includes(c)}, getAttribute: () => null,
  matches(sel) { return sel.split(",").map(s => s.trim()).includes("#" + id); }, closest() { return this; }});
global.document = {documentElement: {dataset: {}}, readyState: "complete", head: {appendChild() {}},
  querySelector: q => (q === 'meta[name="theme-color"]' ? {} : q === ".mic-btn.on" ? (recOn ? {} : null) : null),
  getElementById: id => (id === "voice" ? {classList: {contains: c => c === "hidden" && voiceHidden}} : null),
  createElement: () => ({}), addEventListener: (t, f) => { listeners[t] = f; }};
const timers = []; global.setTimeout = (f) => timers.push(f);
eval(SRC);
const click = (e) => { performance.t += 1000; listeners.click({target: e}); timers.splice(0).forEach(f => f()); };
const out = {};
click(el("send")); out.tap = tones.length; out.peak = Math.max(...gains.map(g => g.gain.max));
click(el("mic")); out.mic_on = tones.length - out.tap;
const n0 = tones.length; voiceHidden = false; click(el("vOrb")); out.in_voice = tones.length - n0;
voiceHidden = true; const n1 = tones.length; click(el("vClose")); out.close = tones.length - n1;
const n2 = tones.length; recOn = true; click(el("chip")); out.while_rec = tones.length - n2; recOn = false;
window.A11Y.setSounds(false); const n3 = tones.length; click(el("send")); out.off = tones.length - n3; out.stored = store.snd;
window.A11Y.setSounds(true); out.back_on = window.A11Y.sounds();
const d = el("send"); d.disabled = true; const n4 = tones.length; click(d); out.disabled = tones.length - n4;
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_tap_sounds():
    import json
    prog = "const SRC = " + json.dumps(JS.read_text(encoding="utf-8")) + ";\n" + PROG
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["tap"] == 1 and 0 < o["peak"] <= 0.08          # one soft blip, quiet
    assert o["mic_on"] == 2 and o["close"] == 2             # two-note cue when the mic opens / the voice dialog closes
    assert o["in_voice"] == 0 and o["while_rec"] == 0        # never into a live microphone
    assert o["off"] == 0 and o["stored"] == "0" and o["back_on"] is True
    assert o["disabled"] == 0
