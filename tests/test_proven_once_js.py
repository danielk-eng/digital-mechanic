"""Local probe 27.11 12:50: replaying the history drew 13 check cards for M333YK47 and fired 13 identical /api/proven requests.
provenFor() shares one request per (vehicle, code) for a minute; a failed one is retried next time (node)."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_one_request_per_vehicle_code():
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("const PROVEN = new Map()"):src.index("async function loadProven")]
    run = """let calls = 0, fail = false; const api = async url => { calls++; if (fail) throw new Error("x"); return {fixes: [url]}; };""" + fn + """
(async () => {
  await Promise.all(Array.from({length: 13}, () => provenFor("M333YK47", "F08")));
  const a = calls; await provenFor("O727CA198", "F08"); const b = calls;
  fail = true; await provenFor("X1", "F01").catch(() => {}); await new Promise(r => setTimeout(r, 0)); fail = false; await provenFor("X1", "F01");
  process.stdout.write([a, b, calls].join(","));
})();"""
    out = subprocess.run(["node", "-e", run], capture_output=True, text=True, check=True, timeout=20).stdout
    assert out == "1,2,4", out
