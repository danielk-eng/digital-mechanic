"use strict";
const $ = s => document.querySelector(s);
let I18N = {}, ME = null, VID = localStorage.getItem("vid") || "", VEH = null, BUSY = false;
const SPEECH_LANG = {ru:"ru-RU",uz:"uz-UZ",tg:"tg-TJ",kk:"kk-KZ",hi:"hi-IN",vi:"vi-VN"};
const t = k => (I18N[(ME && ME.lang) || $("#l_lang")?.value || "ru"] || I18N.ru || {})[k] || (I18N.ru || {})[k] || k;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(path, opts = {}) {
  const o = {credentials: "same-origin", ...opts};
  if (o.json) { o.method = o.method || "POST"; o.headers = {"Content-Type": "application/json"}; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch(path, o);
  if (!r.ok) { const e = new Error((await r.text()).slice(0, 300)); e.status = r.status; throw e; }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}

function applyI18n() {
  document.querySelectorAll("[data-t]").forEach(el => el.textContent = t(el.dataset.t));
  document.querySelectorAll("[data-tp]").forEach(el => el.placeholder = t(el.dataset.tp));
  document.documentElement.lang = (ME && ME.lang) || "ru";
  loadChips();
}

// Adaptive chips: built on the server from this vehicle, its current check, history and repair step.
let CHIP_SEQ = 0;
async function loadChips() {
  const q = $("#quick"); if (!q || !ME || !VID) { if (q) q.innerHTML = ""; return; }
  const seq = ++CHIP_SEQ;
  try {
    const r = await api(`/api/suggest?vehicle_id=${encodeURIComponent(VID)}`);
    if (seq !== CHIP_SEQ) return;
    q.innerHTML = "";
    (r.chips || []).forEach(ch => {
      const b = document.createElement("button"); b.textContent = ch.text; b.className = `qc ${ch.kind}`;
      if (ch.page) b.title = `${ch.doc} · ${t("page")} ${ch.page}`;
      b.onclick = ch.kind === "triage" ? () => eventTurn("triage", "", ch.text) : ch.kind === "code" ? () => { $("#txt").value = "F0"; $("#txt").focus(); } : () => send(ch.text); q.appendChild(b);
    });
  } catch (_) { /* chips are optional */ }
}

function langOptions(sel, cur) {
  sel.innerHTML = Object.entries({ru:"Русский",uz:"Oʻzbekcha",tg:"Тоҷикӣ",kk:"Қазақша",hi:"हिन्दी",vi:"Tiếng Việt"})
    .map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${v}</option>`).join("");
}

// ---------------------------------------------------------------- markdown-lite with source labels
function md(text, sources) {
  const src = Object.fromEntries((sources || []).map(s => [s.label, s]));
  let h = esc(text);
  h = h.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  h = h.replace(/^#{1,4}\s*(.+)$/gm, "<b>$1</b>");
  h = h.replace(/^\s*[-•*]\s+/gm, "• ");
  h = h.replace(/\[([^\]]{1,80})\]/g, (m, grp, off, whole) => { const labs = grp.match(/\b(?:S|X)\d+\b/g); if (!labs) return m;
    const ctx = whole.slice(Math.max(0, off - 240), off).replace(/<[^>]+>/g, "").split(/\n|[.!?]\s|•/).filter(x => x.trim().length > 6).pop() || "";
    return labs.map(l => src[l] ? `<span class="chip ${src[l].extra ? "x" : ""}" data-doc="${src[l].doc_id}" data-page="${src[l].page}" data-q="${ctx.trim().slice(0, 200)}">${esc(src[l].document.slice(0, 24))}, ${t("page")} ${src[l].page}</span>` : "").join(""); });
  return h.replace(/\n/g, "<br>");
}

function addMsg(role, html, cls = "") {
  const d = document.createElement("div"); d.className = `msg ${role}`;
  d.innerHTML = `<div class="b ${cls}">${html}</div>`;
  $("#chat").appendChild(d); d.scrollIntoView({behavior: "smooth", block: "end"});
  return d;
}

function pagesHtml(pages) {
  return pages && pages.length ? `<div class="muted" style="margin-top:.4em">` + pages.map(s => `<span class="chip ${s.extra ? "x" : ""}" data-doc="${s.doc_id}" data-page="${s.page}">${esc(s.document.slice(0, 30))}, ${t("page")} ${s.page}</span>`).join("") + "</div>" : "";
}
function sourcesHtml(sources) {
  if (!sources || !sources.length) return "";
  return `<div class="muted" style="margin-top:.4em">${t("source")}: ` + sources.map(s =>
    `<span class="chip ${s.extra ? "x" : ""}" data-doc="${s.doc_id}" data-page="${s.page}">${esc(s.document.slice(0, 40))}, ${t("page")} ${s.page}${s.extra ? " *" : ""}</span>`).join("") + "</div>";
}

const FACT_ICON = {danger: "⚠️", tool: "🔧", torque: "🔩", value: "📏"};
function factsHtml(facts) {  // verbatim lines from the cited pages: danger / tools / torques / control values
  if (!facts || !facts.length) return "";
  return `<div class="facts"><b>${t("key_facts")}</b>` + facts.map(f => `<div class="fact ${f.kind}">${FACT_ICON[f.kind] || "•"} ${esc(f.text)} ` +
    `<span class="chip" data-doc="${esc(f.doc_id)}" data-page="${f.page}" data-q="${esc(f.text.slice(0, 200))}">${t("page")} ${f.page}</span></div>`).join("") + `</div>`;
}

function renderTurn(turn, expanded = false) {
  (turn.cards || []).forEach(renderCard);
  if (turn.text) {
    const canExpand = !expanded && (turn.sources || []).length && (ME.detail || "brief") === "brief";
    const canAlt = VID && (turn.sources || []).length;
    const und = (turn.understood || []).length ? `<div class="muted understood">🔎 ${t("understood")}: ${turn.understood.map(([a, b]) => `${esc(a)} → <b>${esc(b)}</b>`).join(", ")}</div>` : "";
    const d = addMsg("bot", und + factsHtml(turn.facts) + md(turn.text, turn.sources) + sourcesHtml(turn.sources) +
      `<div class="row acts">` + (canExpand ? `<button class="expand">${t("expand")} ▾</button>` : "") +
      (canAlt ? `<button class="expand alt">🔀 ${t("alternatives")}</button><button class="expand hyp">🧪 ${t("hypothesis")}</button>` : "") +
      ((turn.sources || []).length ? `<button class="expand prn">🖨 ${t("print")}</button>` : "") + `</div>`);
    const prn = d.querySelector(".prn"); if (prn) prn.onclick = ev => { ev.stopPropagation(); printTurn(turn); };
    if (canExpand) d.querySelector(".expand").onclick = ev => { ev.stopPropagation(); ev.target.remove(); expandAnswer(turn.text); };
    if (canAlt) {
      d.querySelector(".alt").onclick = ev => { ev.stopPropagation(); ev.target.remove(); eventTurn("alternatives", "", "🔀 " + t("alternatives")); };
      d.querySelector(".hyp").onclick = ev => { ev.stopPropagation(); HYP = true; $("#txt").placeholder = t("hypothesis_ph"); $("#txt").focus(); };
    }
    speak(turn.text);
  }
  loadChips();
}
let HYP = false;
// item 19: printable / Save-as-PDF procedure: answer + verbatim key facts + labor norms + the cited manual pages themselves
async function printTurn(turn) {
  const w = window.open("", "_blank"); if (!w) return;
  w.document.write(`<p>${t("thinking")}</p>`);
  const veh = $("#vehicle") && $("#vehicle").selectedOptions[0] ? $("#vehicle").selectedOptions[0].textContent : "";
  const labor = (turn.cards || []).flatMap(c => c.labor || []);
  const lab = labor.length ? `<p>⏱ ${t("labor")}: ` + (labor.some(l => l.norm) ? labor.filter(l => l.norm).map(l => `${esc(l.norm)} (${t("page")} ${l.page})`).join("; ") : t("labor_na")) + `</p>` : "";
  const seen = new Set(), pages = [];
  for (const s of (turn.sources || [])) { const k = s.doc_id + "#" + s.page; if (seen.has(k) || pages.length >= 6) continue; seen.add(k);
    let info = {}; try { info = await api(`/api/page/${encodeURIComponent(s.doc_id)}/${s.page}`); } catch (e) {}
    pages.push(`<section class="pg"><h3>${esc(s.label || "")} · ${esc(info.full_title || s.document)} — ${t("page")} ${s.page}${s.extra ? " *" : ""}</h3>` +
      (info.has_image ? `<img src="/page/${encodeURIComponent(s.doc_id)}/${s.page}.png">` : `<pre>${esc((info.text || "").slice(0, 4000))}</pre>`) + `</section>`); }
  const css = `body{font:13pt/1.45 system-ui,sans-serif;color:#000;margin:1.5cm}h1{font-size:16pt;margin:0}h3{font-size:11pt;margin:.6em 0 .3em}
    .chip{font-size:.8em;color:#333}.chip:before{content:"["}.chip:after{content:"]"}.facts{border:1px solid #999;padding:.4em .7em;margin:.6em 0}
    .fact{margin:.2em 0}img{max-width:100%;border:1px solid #ccc}pre{white-space:pre-wrap;font-size:9.5pt;border:1px solid #ccc;padding:.5em}
    .pg{page-break-before:always}.muted{color:#444;font-size:.9em}@media print{.noprint{display:none}}`;
  w.document.open();
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(t("app_title"))} — ${esc(veh)}</title><style>${css}</style></head><body>
    <p class="noprint"><button onclick="print()">🖨 ${t("print")}</button></p>
    <h1>${esc(t("app_title"))}</h1><p class="muted">${esc(veh)} · ${new Date().toLocaleString()} · ${esc((ME && ME.name) || "")}</p>
    ${factsHtml(turn.facts)}<div>${md(turn.text, turn.sources)}</div>${lab}
    <p class="muted">${t("print_note")}</p>${pages.join("")}</body></html>`);
  w.document.close();
  const imgs = [...w.document.images]; await Promise.all(imgs.map(i => i.complete ? 1 : new Promise(r => { i.onload = i.onerror = r; })));
  w.focus(); w.print();
}
async function eventTurn(event, text, shown) {
  stopSpeak();
  if (shown) addMsg("user", esc(shown));
  const th = addMsg("bot", `<span class="thinking">${t("thinking")}</span>`);
  try { const r = await api("/api/chat", {json: {vehicle_id: VID || null, text, event}}); th.remove(); renderTurn(r); }
  catch (e) { th.innerHTML = `<div class="b" style="color:var(--stop)">${esc(e.message)}</div>`; }
}
async function expandAnswer(text) {
  stopSpeak();
  const th = addMsg("bot", `<span class="thinking">${t("thinking")}</span>`);
  try { const r = await api("/api/chat", {json: {vehicle_id: VID || null, text, event: "expand"}}); th.remove(); renderTurn(r, true); }
  catch (e) { th.innerHTML = `<div class="b" style="color:var(--stop)">${esc(e.message)}</div>`; }
}

// ---------------------------------------------------------------- cards
function spark(trend) {
  const pts = (trend || []).filter(p => p.anomaly_score != null);
  if (pts.length < 2) return "";
  const W = 240, H = 60, n = trend.length;
  const xy = trend.map((p, i) => [10 + i * (W - 20) / Math.max(n - 1, 1), p.anomaly_score == null ? null : H - 8 - p.anomaly_score * (H - 16)]);
  const line = xy.filter(p => p[1] != null).map(p => p.join(",")).join(" ");
  const col = s => s === "anomaly" ? "#e5484d" : s === "normal" ? "#2fbf71" : "#3d8bfd";
  return `<svg width="${W}" height="${H}" style="background:#11181e;border-radius:8px"><line x1="0" x2="${W}" y1="${H - 8 - 0.5 * (H - 16)}" y2="${H - 8 - 0.5 * (H - 16)}" stroke="#555" stroke-dasharray="3"/><polyline fill="none" stroke="#ffb020" stroke-width="2" points="${line}"/>` +
    xy.map((p, i) => `<circle cx="${p[0]}" cy="${p[1] ?? H - 8}" r="4" fill="${col(trend[i].status)}"><title>${trend[i].session_id} ${trend[i].status} ${trend[i].anomaly_score ?? "—"}</title></circle>`).join("") + "</svg>";
}

async function loadProven(box, code) {  // item 6: proven fixes for this engine + fault code in this fleet
  try {
    const r = await api(`/api/proven?vehicle_id=${encodeURIComponent(VID)}&code=${encodeURIComponent(code)}`);
    if (!r.fixes.length) return;
    const ver = f => f.verified === true ? `✅ ${t("fix_verified")}` : f.verified === false ? `⚠️ ${t("fix_not_verified")}` : "";
    box.insertAdjacentHTML("beforeend", `<details class="proven"><summary>🛠 ${t("proven_fixes")} (${r.engine}, ${esc(code)}): ${r.fixes.length}</summary>` +
      r.fixes.map(f => `<div class="fix"><b>${esc(f.vehicle_id)}</b> · ${esc(f.date || "")} · ${esc(f.ticket_id || "")} ${ver(f)}${f.source === "demo" ? ` <span class="muted">(${t("demo_hist")})</span>` : ""}<ol>` +
        f.steps.map(st => `<li>${esc(st.text)}${st.page ? ` <span class="chip" data-doc="${esc(st.doc_id)}" data-page="${st.page}" data-q="${esc(st.text)}">${t("page")} ${st.page}</span>` : ""}</li>`).join("") + `</ol>` +
        (f.parts && f.parts.length ? `<div class="muted">${t("parts")}: ${f.parts.map(esc).join(", ")}</div>` : "") + `</div>`).join("") + `</details>`);
  } catch (_) {}
}

function renderCard(c) {
  if (c.kind === "verdict") {
    const ch = c.check, st = {normal: t("status_normal"), anomaly: t("status_anomaly"), check_failed: t("status_failed")}[c.status];
    const head = c.status === "anomaly" ? `${st}: ${esc(ch.anomaly_title)}` : c.status === "normal" ? t("verdict_normal") : t("verdict_failed");
    let h = `<div class="big">${c.light === "stop" ? "⛔" : c.light === "service" ? "⚠️" : c.light === "repeat" ? "🔁" : "✅"} ${head}</div>`;
    if (c.light_text) h += `<div style="margin:.3em 0 .5em;font-weight:600">${esc(c.light_text)}</div>`;
    h += `<div class="kv"><span class="muted">${t("session")}</span><span>${esc(ch.session_id)} · ${esc((ch.checked_at || "").slice(0, 16).replace("T", " "))}</span>`;
    if (ch.anomaly_score != null) h += `<span class="muted">${t("score")}</span><span>${ch.anomaly_score.toFixed(2)}</span>`;
    if (ch.confidence != null) h += `<span class="muted">${t("confidence")}</span><span>${Math.round(ch.confidence * 100)}%</span>`;
    h += `<span class="muted">${t("quality")}</span><span>${esc(ch.signal_quality)}</span><span class="muted">ML</span><span>${esc(ch.description)}</span></div>`;
    if (c.doubt && c.doubt.length) h += `<div style="margin-top:.5em;color:var(--warn)">⚠️ ${t("doubt")}: ${c.doubt.map(esc).join("; ")}</div>`;
    if (c.urgency_basis) h += `<details style="margin-top:.4em"><summary class="muted">Приоритет ${esc(c.urgency)} — почему</summary><div class="muted">${c.urgency_basis.map(esc).join("<br>")}</div></details>`;
    if (c.trend) h += `<div style="margin-top:.5em" class="muted">${t("history")}:</div>${spark(c.trend)}`;
    const vd = addMsg("bot", h, `card verdict ${c.light}`);
    if (c.status === "anomaly" && ch.anomaly_type) loadProven(vd.querySelector(".b"), ch.anomaly_type);
  } else if (c.kind === "ticket_draft") {
    const r = c.request;
    let h = `<b>📝 ${t("create_ticket")}</b> — <span class="badge b-${r.urgency === "high" ? "quarantine" : r.urgency === "medium" ? "attention" : "ready"}">${r.urgency}</span>`;
    h += `<div style="margin:.4em 0">${esc(r.conclusion)}</div><b>Работы:</b><ol>` + c.operations.map(o => `<li>${esc(o.text)} <span class="chip" data-doc="${o.doc_id}" data-page="${o.page}" data-q="${esc(o.quote)}">${esc(o.document.slice(0, 22))}, ${t("page")} ${o.page}</span></li>`).join("") + "</ol>";
    if (c.spare_parts.length) h += `<b>Запчасти:</b><ul>` + c.spare_parts.map(o => `<li>${esc(o.text)} <span class="chip" data-doc="${o.doc_id}" data-page="${o.page}" data-q="${esc(o.quote)}">${t("page")} ${o.page}</span></li>`).join("") + "</ul>";
    else h += `<div class="muted">Запчасти: не требуются по найденной процедуре</div>`;
    if (c.labor) { const known = c.labor.filter(l => l.norm);
      h += `<div class="muted" style="margin-top:.3em">⏱ ${t("labor")}: ` + (known.length ? known.map(l => `${esc(l.norm)} <span class="chip" data-doc="${esc(l.doc_id)}" data-page="${l.page}" data-q="${esc(l.norm)}">${t("page")} ${l.page}</span>`).join("; ") +
        (known.length < c.labor.length ? `; ${t("labor_na_some")}` : "") : t("labor_na")) + `</div>`; }
    h += `<details><summary class="muted">JSON для ТОиР</summary><pre style="white-space:pre-wrap;font-size:.75em">${esc(JSON.stringify(r, null, 1))}</pre></details>`;
    const d = addMsg("bot", h + `<div class="row" style="margin-top:.6em"></div>`, "card");
    const row = d.querySelector(".row:last-child");
    const ok = document.createElement("button"); ok.className = "go"; ok.textContent = c.requires_inspection ? t("inspection_ok") : t("confirm_send");
    ok.onclick = () => confirmTicket(c.draft_id, true, row);
    row.appendChild(ok);
    const no = document.createElement("button"); no.textContent = c.requires_inspection ? t("inspection_no") : t("cancel");
    no.onclick = () => c.requires_inspection ? confirmTicket(c.draft_id, false, row) : (row.innerHTML = `<span class="muted">${t("cancel")}</span>`, send(t("cancel")));
    row.appendChild(no);
  } else if (c.kind === "ticket_created") {
    addMsg("bot", `<div class="big">✅ ${t("ticket_created")}: <b>${esc(c.ticket_id)}</b></div><div class="muted">${esc(c.request.conclusion)}</div>`, "card verdict go");
  } else if (c.kind === "repair") {
    const n = c.steps.length, cur = c.current;
    let h = `<b>🛠 ${esc(c.title)}</b> — ${c.status === "done" ? t("repair_done") : `${t("step")} ${Math.min(cur + 1, n)} ${t("of")} ${n}`}<ol class="steps">` +
      c.steps.map((s, i) => `<li class="${s.done ? "done" : i === cur ? "cur" : ""}">${esc(s.text)} <span class="chip" data-doc="${s.doc_id}" data-page="${s.page}" data-q="${esc(s.quote)}">${t("page")} ${s.page}</span></li>`).join("") + "</ol>";
    const d = addMsg("bot", h + (c.status === "done" ? "" : `<div class="row"></div>`), "card");
    if (c.status !== "done") {
      const row = d.querySelector(".row");
      const b1 = document.createElement("button"); b1.className = "go"; b1.style.flex = "1"; b1.textContent = "✔ " + t("next_step"); b1.onclick = () => repairAction("done");
      const b2 = document.createElement("button"); b2.textContent = "↩"; b2.onclick = () => repairAction("back");
      row.append(b1, b2);
    }
  }
}

async function confirmTicket(draftId, ok, row) {
  row.innerHTML = `<span class="thinking">…</span>`;
  try {
    const r = await api("/api/ticket/confirm", {json: {vehicle_id: VID, draft_id: draftId, inspection_ok: ok}});
    row.innerHTML = "";
    if (r.card) renderCard(r.card);
    else addMsg("bot", esc(r.text || r.error || ""));
    speak(r.text || "");
  } catch (e) { row.innerHTML = `<span style="color:var(--stop)">${esc(e.message)}</span>`; }
}

async function repairAction(a) {
  const r = await api("/api/repair/action", {json: {vehicle_id: VID, action: a}});
  if (r.card) { renderCard(r.card); loadChips(); const s = r.card.steps[r.card.current]; if (s && r.card.status !== "done") speak(`${t("step")} ${r.card.current + 1}. ${s.text}`); else speak(t("repair_done")); }
}

// ---------------------------------------------------------------- page viewer
let LAST_Q = "";
function hl(txt, q) {  // highlight the cited words in the section text
  const words = [...new Set((q || "").toLowerCase().match(/[\p{L}\d]{5,}/gu) || [])].slice(0, 12);
  if (!words.length) return txt;
  const re = new RegExp(`(${words.map(w => w.slice(0, Math.max(5, w.length - 2)).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})[\\p{L}]*`, "giu");
  return txt.replace(re, m => `<mark>${m}</mark>`);
}
document.addEventListener("click", async e => {
  const ch = e.target.closest(".chip[data-doc]");
  if (!ch) return;
  const doc = ch.dataset.doc, page = +ch.dataset.page, q = ch.dataset.q || "";
  $("#modal").classList.remove("hidden"); $("#mBody").innerHTML = "…";
  try {
    const s = await api(`/api/section/${encodeURIComponent(doc)}/${page}?q=${encodeURIComponent(q || LAST_Q)}`);
    $("#mTitle").textContent = `${s.full_title}`;
    $("#mPdf").href = s.pdf ? `/manuals/${encodeURIComponent(doc)}.pdf#page=${page}` : "#";
    $("#mPdf").classList.toggle("hidden", !s.pdf);
    const link = n => s.pdf ? `<a class="chip" target="_blank" href="/manuals/${encodeURIComponent(doc)}.pdf#page=${n}">${t("page")} ${n}</a>`
      : `<span class="chip pg" data-img="${n}">${t("page")} ${n}</span>`;
    const pages = s.pages.map(p => p.page);
    $("#mBody").innerHTML = (s.extra ? `<div class="muted">* ${t("extra_src")}</div>` : "") +
      `<div class="extract" id="mExtract"><span class="muted">⏳ ${t("extract")}…</span></div>` +
      `<div class="sechead"><b>${esc(s.label || t("page") + " " + page)}</b><div class="muted">${t("pages")}: ${pages.map(link).join(" ")}</div></div>` +
      `<details class="sec"><summary>${t("full_section")} (${t("page")} ${esc(s.span)})</summary>` +
      s.pages.map(p => `<div class="muted">— ${t("page")} ${p.page} —</div><pre>${hl(esc(p.text), q)}</pre>`).join("") + `</details>` +
      (s.has_image ? `<details class="sec" id="mImgBox"><summary>${t("page_image")} ${page}</summary><img id="mImg" loading="lazy" src="/page/${encodeURIComponent(doc)}/${page}.png" alt="page"></details>` : "");
    $("#mBody").querySelectorAll(".chip.pg").forEach(c => c.onclick = ev => { ev.stopPropagation(); const n = c.dataset.img;
      if ($("#mImg")) { $("#mImg").src = `/page/${encodeURIComponent(doc)}/${n}.png`; $("#mImgBox").open = true; $("#mImgBox summary").textContent = `${t("page_image")} ${n}`; } });
    api("/api/extract", {json: {doc_id: doc, page, q, question: LAST_Q}}).then(x => {
      const box = $("#mExtract"); if (!box) return;
      box.innerHTML = x.ok ? `<b>${t("extract")}:</b> ${md(x.text)}` : `<span class="muted">${t("extract_na")}</span>`;
    }).catch(() => { const box = $("#mExtract"); if (box) box.innerHTML = `<span class="muted">${t("extract_na")}</span>`; });
  } catch (err) { $("#mBody").textContent = err.message; }
});
$("#mClose").onclick = () => $("#modal").classList.add("hidden");

// ---------------------------------------------------------------- vehicles / diagnosis / chat
// item 22: quick entry by plate or garage number — typed or dictated, Cyrillic/Latin look-alikes, spaces and dashes ignored
let VLIST = [];
const LAT = {"А":"A","В":"B","Е":"E","К":"K","М":"M","Н":"H","О":"O","Р":"P","С":"C","Т":"T","У":"Y","Х":"X"};
const vnorm = s => (s || "").toUpperCase().replace(/[АВЕКМНОРСТУХ]/g, c => LAT[c]).replace(/[^A-ZА-ЯЁ0-9]/g, "");
function findVehicles(q) {
  const n = vnorm(q); if (n.length < 2) return [];
  const dig = /^\d+$/.test(n);
  const exact = VLIST.filter(v => vnorm(v.vehicle_id) === n || vnorm(v.garage_no) === n || (dig && vnorm(v.garage_no).replace(/\D/g, "") === n));
  if (exact.length) return exact;
  return VLIST.filter(v => vnorm(v.vehicle_id).includes(n) || vnorm(v.garage_no).includes(n)).slice(0, 6);
}
function plateInText(text) {  // "В332ВР98", "в 332 вр 98", "Г-141" inside a free sentence
  const m = vnorm(text).match(/[ABEKMHOPCTYX]\d{3}[ABEKMHOPCTYX]{2}\d{2,3}/);
  if (m) { const f = findVehicles(m[0]); if (f.length === 1) return f[0]; }
  const g = (text || "").match(/г(?:ар(?:аж(?:ный)?)?)?\.?\s*(?:№|n|номер)?\s*-?\s*(\d{2,4})/i);
  if (g) { const f = VLIST.filter(v => vnorm(v.garage_no).replace(/\D/g, "") === g[1]); if (f.length === 1) return f[0]; }
  return null;
}
$("#vfind").oninput = () => {
  const f = findVehicles($("#vfind").value);
  if (f.length === 1) { $("#vfind").value = ""; $("#vfindRes").innerHTML = ""; $("#vehicle").value = f[0].vehicle_id; return selectVehicle(f[0].vehicle_id); }
  $("#vfindRes").innerHTML = f.map(v => `<button class="expand" data-v="${esc(v.vehicle_id)}">${esc(v.vehicle_id)} · ${esc(v.garage_no || "")} · ${esc(v.model || "")}</button>`).join("") ||
    (vnorm($("#vfind").value).length >= 3 ? `<span class="muted">${t("not_found")}</span>` : "");
  $("#vfindRes").querySelectorAll("button").forEach(b => b.onclick = () => { $("#vfind").value = ""; $("#vfindRes").innerHTML = ""; $("#vehicle").value = b.dataset.v; selectVehicle(b.dataset.v); });
};

async function loadVehicles() {
  const r = await api("/api/vehicles"); VLIST = r.vehicles;
  const sel = $("#vehicle");
  sel.innerHTML = `<option value="">${t("choose_vehicle")}</option>` + r.vehicles.map(v =>
    `<option value="${v.vehicle_id}">${v.vehicle_id} · ${esc(v.model || "")} · ${esc(v.engine_model)}${v.in_repair ? " 🛠" : ""}</option>`).join("");
  if (VID && r.vehicles.some(v => v.vehicle_id === VID)) sel.value = VID; else VID = "";
  sel.onchange = () => selectVehicle(sel.value);
  if (VID) selectVehicle(VID);
}

async function selectVehicle(vid) {
  VID = vid; localStorage.setItem("vid", vid); $("#chat").innerHTML = ""; loadChips();
  if (!vid) { $("#vehInfo").classList.add("hidden"); return; }
  const r = await api(`/api/vehicle/${encodeURIComponent(vid)}`);
  VEH = r;
  const v = r.vehicle;
  $("#vehInfo").classList.remove("hidden");
  $("#vehInfo").innerHTML = `<b>${esc(v.vehicle_id)}</b> · ${esc(v.model && v.model.startsWith(v.make) ? v.model : v.make + " " + (v.model || ""))} · ${esc(v.vehicle_type)}<br>⚙️ <b>${esc(v.engine_model)}</b> (${esc(v.fuel)}) · гараж. ${esc(v.garage_no)}<div class="scope" title="${esc(r.manuals.map(m => m.title).join(" · "))}">🔎 ${t("scope_only")} <b>${esc(v.engine_model)}</b> · ${r.manuals.length} ${t("books")}</div><div class="muted">📚 ${r.manuals.map(m => esc(m.title)).join(" · ")}</div>` +
    (r.tickets.length ? `<div class="muted">${t("tickets")}: ${r.tickets.slice(0, 3).map(x => esc(x.ticket_id)).join(", ")}</div>` : "") +
    `<div class="row acts"><button id="btnHist" class="expand">🗂 ${t("history")}</button><button id="btnSum" class="expand">📝 ${t("summary")}</button><button id="btnHand" class="expand">🙋 ${t("handoff")}</button><button id="btnInsp" class="expand">🔍 ${t("inspection")}</button></div><div id="arrBox"></div><div id="histBox" class="hidden"></div>`;
  api(`/api/history/${encodeURIComponent(vid)}`).then(tl => { if (VID === vid && $("#arrBox")) $("#arrBox").innerHTML = arrivalCard(tl, t); }).catch(() => {});
  $("#btnSum").onclick = async () => {
    const th = addMsg("bot", `<span class="thinking">${t("thinking")}</span>`);
    try { const s = await api("/api/summary", {json: {vehicle_id: VID}}); th.querySelector(".b").innerHTML = `<b>📝 ${t("summary")}</b><br>` + md(s.text) + pagesHtml(s.pages); }
    catch (e) { th.querySelector(".b").textContent = e.message; }
  };
  $("#btnInsp").onclick = async () => {
    const th = addMsg("bot", `<span class="thinking">${t("thinking")}</span>`);
    try {
      const r = await api("/api/inspection/draft", {json: {vehicle_id: VID}}); const b = th.querySelector(".b");
      if (!r.items.length) { b.textContent = r.note; return; }
      const sev = {"норма": "🟢", "внимание": "🟡", "дефект": "🔴"};
      b.innerHTML = `<b>🔍 ${t("inspection")}</b> <span class="muted">${t("insp_review")}</span>` + r.items.map((it, i) =>
        `<div class="insp"><label><input type="checkbox" data-i="${i}" checked> ${sev[it.severity] || "🟡"} <b contenteditable="true" data-f="component">${esc(it.component)}</b>: <span contenteditable="true" data-f="finding">${esc(it.finding)}</span></label> <span class="muted">(${esc(it.source)})</span>` +
        (it.page ? ` <span class="chip" data-doc="${esc(it.doc_id)}" data-page="${it.page}">${t("page")} ${it.page}</span>` : "") + `</div>`).join("") +
        `<div class="row" style="margin-top:.5em"><button class="go" id="inspOk">✅ ${t("insp_confirm")}</button></div>`;
      b.querySelector("#inspOk").onclick = async () => {
        const items = r.items.map((it, i) => { const row = b.querySelectorAll(".insp")[i];
          return {...it, component: row.querySelector('[data-f="component"]').textContent.trim(), finding: row.querySelector('[data-f="finding"]').textContent.trim(), confirmed: row.querySelector("input").checked}; });
        try { const s = await api("/api/inspection/confirm", {json: {vehicle_id: VID, items}}); b.querySelector(".row").innerHTML = `✅ ${t("insp_saved")} №${s.id}`; }
        catch (e) { alert(e.message); }
      };
    } catch (e) { th.querySelector(".b").textContent = e.message; }
  };
  $("#btnHand").onclick = async () => {
    const note = prompt(t("handoff_note")); if (note === null) return;
    const th = addMsg("bot", `<span class="thinking">${t("thinking")}</span>`);
    try { const s = await api("/api/handoff", {json: {vehicle_id: VID, note}}); th.querySelector(".b").innerHTML = `<b>🙋 ${t("handoff_done")} №${s.id}</b><br>` + md(s.summary) + pagesHtml(s.pages); }
    catch (e) { th.querySelector(".b").textContent = e.message; }
  };
  $("#btnHist").onclick = async () => {
    const box = $("#histBox");
    if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
    box.innerHTML = renderTimeline(await api(`/api/history/${encodeURIComponent(vid)}`)); box.classList.remove("hidden");
  };
  $("#sess").innerHTML = r.sessions.slice().reverse().map((s, i) => `<option value="${s.session_id}">${s.session_id}${i === 0 ? " (" + t("latest") + ")" : ""} · ${s.checked_at.slice(5, 10)}</option>`).join("");
  for (const m of r.messages) {
    (m.meta.cards || []).forEach(renderCard);
    if (m.text) addMsg(m.role === "user" ? "user" : "bot", m.role === "user" ? esc(m.text) : md(m.text, m.meta.sources) + sourcesHtml(m.meta.sources));
  }
  if (r.draft && !r.messages.some(m => (m.meta.cards || []).some(c => c.kind === "ticket_draft"))) renderCard(r.draft);
  if (!r.messages.length && r.repair) renderCard({...r.repair, kind: "repair"});
}

async function diagnose() {
  stopSpeak();
  if (!VID) { const v = plateInText(text); if (!v) return alert(t("choose_vehicle"));
    $("#vehicle").value = v.vehicle_id; return selectVehicle(v.vehicle_id).then(() => send(text)); }
  if (BUSY) return; BUSY = true; $("#btnDiag").disabled = true;
  try {
    const r = await api("/api/diagnose", {json: {vehicle_id: VID, session_id: $("#sess").value || null}});
    renderCard(r.card); loadChips();
    const head = r.card.status === "anomaly" ? `${t("status_anomaly")}: ${r.card.check.anomaly_title}. ${r.card.light_text || ""}` : r.card.status === "normal" ? t("verdict_normal") : t("verdict_failed");
    speak(head);
    BUSY = false;
    await ask("", "explain_diagnosis");
  } catch (e) { addMsg("bot", `<span style="color:var(--stop)">${esc(e.message)}</span>`); }
  finally { BUSY = false; $("#btnDiag").disabled = false; }
}

async function ask(text, event = null) {
  if (BUSY) return; BUSY = true;
  const th = addMsg("bot", `<span class="thinking">${t("thinking")}</span>`);
  const t0 = Date.now();
  let steps = [];
  const tick = setInterval(async () => {
    const sec = Math.round((Date.now() - t0) / 1000);
    if (sec % 2 === 0) { try { steps = (await api(`/api/progress?vehicle_id=${encodeURIComponent(VID || "")}`)).steps; } catch (_) {} }
    const el = th.querySelector(".thinking"); if (el) el.innerHTML = `${t("thinking")} ${sec} c` + (steps.length ? "<br>" + steps.slice(-3).map(esc).join("<br>") : "");
  }, 1000);
  try {
    const r = await api("/api/chat", {json: {vehicle_id: VID || null, text, event}});
    th.remove(); renderTurn(r);
  } catch (e) { th.innerHTML = `<div class="b" style="color:var(--stop)">${esc(e.message)}</div>`; }
  finally { clearInterval(tick); BUSY = false; }
}

function send(text) {
  stopSpeak();
  text = (text ?? $("#txt").value).trim();
  if (text) LAST_Q = text;
  if (HYP && text) { HYP = false; $("#txt").value = ""; $("#txt").placeholder = t("ask_placeholder"); return eventTurn("hypothesis", text, "🧪 " + text); }
  if (!text) return;
  if (!VID) return alert(t("choose_vehicle"));
  $("#txt").value = "";
  addMsg("user", esc(text));
  ask(text);
}

// ---------------------------------------------------------------- photo
$("#photo").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  const url = URL.createObjectURL(f);
  addMsg("user", `<img src="${url}" style="max-width:220px;border-radius:8px"><br>${esc($("#txt").value)}`);
  const fd = new FormData(); fd.append("file", f); fd.append("vehicle_id", VID); fd.append("text", $("#txt").value); $("#txt").value = "";
  const th = addMsg("bot", `<span class="thinking">${t("thinking")}</span>`);
  try {
    const r = await api("/api/photo", {method: "POST", body: fd});
    th.remove();
    if (r.description) addMsg("bot", `<span class="muted">📷 ${esc(r.description)}</span>`);
    if (r.plate && r.plate !== VID) {
      const d = addMsg("bot", `🚚 ${t("vehicle")}: <b>${esc(r.plate)}</b>? <button class="go">OK</button>`);
      d.querySelector("button").onclick = () => { $("#vehicle").value = r.plate; selectVehicle(r.plate); };
    }
    if (r.turn) renderTurn(r.turn);
  } catch (err) { th.innerHTML = `<div class="b" style="color:var(--stop)">${esc(err.message)}</div>`; }
  e.target.value = "";
};

// ---------------------------------------------------------------- voice in (Deepgram/whisper via server, browser fallback)
let rec = null, chunks = [], stream = null, vadTimer = null, SERVER_TTS = true, SPEAK_SEQ = 0;
async function startRec() {
  stopSpeak();
  if (rec) return stopRec();
  try { stream = await navigator.mediaDevices.getUserMedia({audio: true}); }
  catch (e) { return browserStt(); }
  chunks = []; rec = new MediaRecorder(stream);
  rec.ondataavailable = e => chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach(tr => tr.stop()); clearInterval(vadTimer);
    $("#mic").classList.remove("rec"); $("#txt").placeholder = t("ask_placeholder");
    const blob = new Blob(chunks, {type: rec.mimeType || "audio/webm"}); rec = null;
    if (blob.size < 2000) return;
    const fd = new FormData(); fd.append("audio", blob, "voice.webm"); fd.append("lang", ME.lang || "ru");
    caption("🎤 " + t("recognizing") + "…");
    try { const r = await api("/api/stt", {method: "POST", body: fd}); if (r.text) { caption("🎤 " + r.text, 5000); send(r.text); } else caption(""); }
    catch (e) { if (e.status === 503) browserStt(); else addMsg("bot", esc(e.message)); }
  };
  rec.start(); $("#mic").classList.add("rec"); $("#txt").placeholder = t("listening"); caption("🎤 " + t("listening"));
  // simple VAD: stop after 1.6 s of silence once speech was heard, max 20 s
  const ctx = new AudioContext(), an = ctx.createAnalyser(); ctx.createMediaStreamSource(stream).connect(an);
  const buf = new Uint8Array(an.fftSize); let heard = false, quiet = 0; const t0 = Date.now();
  vadTimer = setInterval(() => {
    an.getByteTimeDomainData(buf); let s = 0; for (const v of buf) s += (v - 128) ** 2; const rms = Math.sqrt(s / buf.length);
    if (rms > 6) { heard = true; quiet = 0; } else if (heard) quiet += 100;
    if ((heard && quiet > 1600) || Date.now() - t0 > 20000) { stopRec(); ctx.close(); }
  }, 100);
}
function stopRec() { if (rec && rec.state !== "inactive") rec.stop(); }
function browserStt() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return alert("Voice input is not available");
  const r = new SR(); r.lang = SPEECH_LANG[ME.lang] || "ru-RU"; r.interimResults = true;
  r.onresult = e => { const res = e.results[e.results.length - 1]; caption("🎤 " + res[0].transcript, res.isFinal ? 5000 : 0); if (res.isFinal) send(res[0].transcript); };
  caption("🎤 " + t("listening")); r.start();
}
$("#mic").onclick = startRec;

// ---------------------------------------------------------------- live captions (item 16)
const CC = { on: () => localStorage.getItem("cc") !== "0", timer: null };
function caption(text, ms = 0) {
  const el = $("#caption"); if (!el) return;
  if (!text || !CC.on()) { el.classList.add("hidden"); return; }
  el.textContent = text; el.classList.remove("hidden");
  clearTimeout(CC.timer); if (ms) CC.timer = setTimeout(() => el.classList.add("hidden"), ms);
}
function sentencesOf(p) { return p.match(/[^.!?;:]+[.!?;:]*/g) || [p]; }
function captionFollow(p, frac) {  // show the sentence at the given fraction of the spoken text
  const ss = sentencesOf(p); let acc = 0; const pos = frac * p.length;
  for (const x of ss) { acc += x.length; if (acc >= pos) return caption(x.trim()); }
}

// ---------------------------------------------------------------- voice out
function plain(text) { return String(text).replace(/\[[^\]]*\b(S|X)\d+\b[^\]]*\]/g, "").replace(/[*#•]/g, "").replace(/\s+/g, " ").slice(0, 900); }
async function speak(text) {
  if (!ME || !A11Y.ttsOn() || !text) return;
  A11Y.stop();
  const p = plain(text), token = ++SPEAK_SEQ;
  const after = () => { if (token === SPEAK_SEQ && $("#s_hands").checked) setTimeout(startRec, 300); };
  const al = ALANG();
  if (al === "ru" && SERVER_TTS) {  // local Silero on the stand; 404 -> browser speech from now on
    try { const r = await fetch("/api/tts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: p})});
      if (token !== SPEAK_SEQ) return;  // a newer message or a tap arrived while synthesizing
      if (r.ok) { const a = new Audio(URL.createObjectURL(await r.blob())); A11Y.audio = a;
        a.ontimeupdate = () => { if (a.duration) captionFollow(p, a.currentTime / a.duration); };
        a.onended = () => { caption(""); after(); }; a.play(); return; } SERVER_TTS = false; } catch (_) { SERVER_TTS = false; }
  }
  if (!window.speechSynthesis) return;
  const u = new SpeechSynthesisUtterance(p); u.lang = SPEECH_LANG[al] || "ru-RU";
  u.onboundary = e => captionFollow(p, (e.charIndex || 0) / p.length); captionFollow(p, 0);
  u.onend = () => { caption(""); after(); }; speechSynthesis.speak(u);
}
const ALANG = () => (ME && ME.answer_lang) || (ME && ME.lang) || "ru";  // item 20: answers may use another language
function stopSpeak() { SPEAK_SEQ++; A11Y.stop(); caption(""); }
$("#chat").addEventListener("click", stopSpeak);  // tap on the chat stops reading

// ---------------------------------------------------------------- settings / login
function applyProfile() {
  langOptions($("#s_lang"), ME.lang);
  langOptions($("#s_alang"), ME.answer_lang || "");
  $("#s_alang").insertAdjacentHTML("afterbegin", `<option value="" ${ME.answer_lang ? "" : "selected"}>${t("same_as_ui")}</option>`);
  document.querySelectorAll("input[name=s_exp]").forEach(r => r.checked = r.value === ME.experience);
  document.querySelectorAll("input[name=s_detail]").forEach(r => r.checked = r.value === (ME.detail || "brief"));
  if (localStorage.getItem("tts") === null) A11Y.setTts(!!ME.voice_out);  // first run: take the old profile flag
  applyI18n();
}
async function saveProfile(patch) { ME = (await api("/api/me", {json: patch})).user; applyProfile(); }
$("#btnSettings").onclick = () => $("#settings").classList.toggle("hidden");
$("#s_lang").onchange = e => saveProfile({lang: e.target.value}).then(loadVehicles);
$("#s_alang").onchange = e => saveProfile({answer_lang: e.target.value});
document.querySelectorAll("input[name=s_exp]").forEach(r => r.onchange = () => saveProfile({experience: r.value}));
document.querySelectorAll("input[name=s_detail]").forEach(r => r.onchange = () => saveProfile({detail: r.value}));
$("#s_logout").onclick = async () => { await api("/api/logout", {method: "POST"}); location.reload(); };
$("#s_delete").onclick = async () => { if (confirm("Удалить ваши данные и историю запросов?")) { await api("/api/me", {method: "DELETE"}); location.reload(); } };
$("#btnDiag").onclick = diagnose;
$("#send").onclick = () => send();
$("#txt").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });

$("#l_go").onclick = async () => {
  const body = {name: $("#l_name").value || "—", role: document.querySelector("input[name=role]:checked").value, lang: $("#l_lang").value,
    experience: document.querySelector("input[name=exp]:checked").value, consent: $("#l_consent").checked};
  if (!body.consent) return alert(t("consent"));
  try { ME = (await api("/api/login", {json: body})).user; } catch (e) { return alert(e.message); }
  if (ME.role === "manager") location.href = "/manager"; else start();
};

async function start() {
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  applyProfile(); await loadVehicles();
}

(async () => {
  I18N = await (await fetch("/static/i18n.json")).json();
  try { ME = (await api("/api/me")).user; start(); }
  catch (e) { langOptions($("#l_lang"), (navigator.language || "ru").slice(0, 2)); $("#l_lang").onchange = applyI18n; applyI18n(); $("#login").classList.remove("hidden"); }
})();

// captions toggle (persisted; on by default)
(() => { const cb = document.getElementById("s_cc"); if (!cb) return; cb.checked = CC.on();
  cb.onchange = () => { localStorage.setItem("cc", cb.checked ? "1" : "0"); if (!cb.checked) caption(""); }; })();
