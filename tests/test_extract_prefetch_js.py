"""Citation extract prefetch (27.11 02:20): the first two distinct page chips of a new answer are fetched in the background and
cached in the client, so tapping a chip reuses the same request (free model: 10–16 s per uncached extract). app.js run in node."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_prefetch_two_distinct_pages_and_reuse_on_open():
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("const BGQ = []"):src.index("async function expandClick")] + src[src.index("const EXTRACTS = new Map()"):src.index("async function openCitation")]
    prog = """
let LAST_Q = "почему стучит", BUSY = false; const VOICE = {on: false}; const calls = [];
function api(path, o) { calls.push(o.json); return o.json.page === 99 ? Promise.reject(new Error("x")) : Promise.resolve({ok: true, text: "t" + o.json.page}); }
const realST = setTimeout; global.setTimeout = f => f();
""" + fn + """
const b = (doc, page, q) => ({dataset: {doc, page: String(page), q}});
VOICE.on = true; prefetchExtracts([b("d", 50, "v")]); if (calls.length) throw new Error("prefetch in voice mode"); VOICE.on = false;
prefetchExtracts([b("d", 52, "a"), b("d", 52, "b"), b("d", 53, "c"), b("d", 54, "e")]);
if (calls.length !== 1 || calls[0].page !== 52) throw new Error("one at a time " + JSON.stringify(calls));
new Promise(r => setImmediate(r)).then(() => {
if (calls.length !== 2 || calls[1].page !== 53 || calls[1].question !== "почему стучит") throw new Error(JSON.stringify(calls));
return getExtract("d", 53, "c", LAST_Q); }).then(x => {
  if (calls.length !== 2 || x.text !== "t53") throw new Error("not reused");
  return getExtract("d", 99, "z", LAST_Q).catch(() => getExtract("d", 99, "z", LAST_Q)).catch(() => {});
}).then(() => { if (calls.length !== 4) throw new Error("failed request must not stay cached " + calls.length); process.stdout.write("OK"); });
"""
    out = subprocess.run(["node", "-e", prog], capture_output=True, text=True)
    assert out.stdout == "OK", out.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_chip_hint_keeps_the_clause_right_before_the_chip():
    """A long «Проверить: …; …» line: the hint kept the first 200 chars, so the last chip (p. 53, «угол опережения впрыскивания»)
    was matched to «промыть заборник» and the extract bolded the oil-pump row (Vercel 27.11 02:20, 390 px)."""
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("function inline(h, src)"):src.index("function collapseCites")]
    text = ("Проверить: топливо в баке и кран всасывающего трубопровода [S1]; герметичность и прокачку системы; разобрать "
            "подкачивающий насос; ход рейки ТНВД [S2]; промыть заборник, продуть трубки [S1]; угол опережения впрыскивания [S3].")
    prog = ("const esc = s => String(s ?? '');const t = x => 'с.';const collapseCites = h => h;" + fn +
            "const h = inline(" + repr(text).replace("'", '"') + ", {S1:{doc_id:'d',page:51}, S2:{doc_id:'d',page:52}, S3:{doc_id:'d',page:53}});"
            "const qs = [...h.matchAll(/data-q=\"([^\"]*)\"/g)].map(m => m[1]); process.stdout.write(qs[qs.length - 1]);")
    out = subprocess.run(["node", "-e", prog], capture_output=True, text=True)
    assert out.stdout.endswith("угол опережения впрыскивания"), out.stdout + out.stderr
    from digital_mechanic.agent.core import table_row_group
    page = ("Причина — Способ устранения\nДвигатель стучит\nРанний впрыск топлива в цилиндры — Отрегулировать угол опережения впрыскивания топлива\n"
            "Пониженное давление масла в системе смазки\nЗасорение заборника масляного насоса — Промыть заборчик масляного насоса")
    head, rows, i = table_row_group(page, out.stdout)
    assert head == "Двигатель стучит" and "Ранний впрыск" in rows[i]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_stream_watchdog_drops_a_hung_request():
    """Vercel 27.11 02:35: a voice question's stream got no answer for 385 s (the server never saw the request). The client
    drops a stream with no headers in time and falls back to the plain request (e.stream)."""
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("const STREAM_WAIT"):src.index("// Ticket draft (10–20 s")]
    prog = fn + """
STREAM_WAIT.head = 50;
global.fetch = (u, o) => new Promise((ok, bad) => o.signal.addEventListener("abort", () => bad(new Error("aborted"))));
const t0 = Date.now();
chatStream({}, () => {}).then(() => process.stdout.write("NO"), e => process.stdout.write(e.stream && e.timeout && Date.now() - t0 < 3000 ? "OK" : "BAD " + e.message));
"""
    out = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20)
    assert out.stdout == "OK", out.stdout + out.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_expand_prefetch_goes_first_one_background_call_at_a_time():
    """«Подробнее» prefetch + 2 extracts: one request at a time (free model slows with parallel calls), the expand first,
    nothing while a question is being answered; a tap while the expand is still queued asks the usual way."""
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("const EXPANDS = new Map()"):src.index("async function eventTurn")] + src[src.index("const EXTRACTS = new Map()"):src.index("async function openCitation")]
    prog = """
let LAST_Q = "q", BUSY = false, VID = "V1", LAST_CHECK = null; const VOICE = {on: false}; const calls = []; const pend = [];
const Player = {stop() {}}; const asked = [];
function eventTurn(ev) { asked.push(ev); return null; }
function api(path, o) { calls.push(o.json.event || ("p" + o.json.page)); return new Promise(r => pend.push(() => r(o.json.event ? {text: "long", sig: "s", sources: []} : {ok: true, text: "t"}))); }
const realST = setTimeout; global.setTimeout = (f, ms) => realST(f, 0);
""" + fn + """
const b = (doc, page) => ({dataset: {doc, page: String(page), q: ""}});
BUSY = true;
prefetchExtracts([b("d", 52), b("d", 53)]);
const turn = {text: "short [S1]", sources: [{label: "S1", doc_id: "d", page: 52}]};
prefetchExpand(turn);
realST(() => {
  if (calls.length) throw new Error("started while busy " + calls);
  BUSY = false;
  realST(() => {
    if (calls.join() !== "expand") throw new Error("expand first, alone: " + calls);
    pend.shift()();
    realST(() => {
      if (calls.join() !== "expand,p52") throw new Error("then extracts one by one: " + calls);
      const t2 = {text: "other [S1]", sources: turn.sources}; BUSY = true; prefetchExpand(t2); BUSY = false;
      // the queued expand of t2 is not started yet (p52 in flight): a tap asks the usual way and drops it from the queue
      expandClick(t2).then(() => { if (asked.join() !== "expand") throw new Error("queued tap " + asked); process.stdout.write("OK"); });
    }, 20);
  }, 3000);
}, 20);
"""
    out = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.stdout == "OK", out.stderr
