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
      b.onclick = () => send(ch.text); q.appendChild(b);
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
    const d = addMsg("bot", factsHtml(turn.facts) + md(turn.text, turn.sources) + sourcesHtml(turn.sources) +
      (canExpand ? `<button class="expand">${t("expand")} ▾</button>` : ""));
    if (canExpand) d.querySelector(".expand").onclick = ev => { ev.stopPropagation(); ev.target.remove(); expandAnswer(turn.text); };
    speak(turn.text);
  }
  loadChips();
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
    addMsg("bot", h, `card verdict ${c.light}`);
  } else if (c.kind === "ticket_draft") {
    const r = c.request;
    let h = `<b>📝 ${t("create_ticket")}</b> — <span class="badge b-${r.urgency === "high" ? "quarantine" : r.urgency === "medium" ? "attention" : "ready"}">${r.urgency}</span>`;
    h += `<div style="margin:.4em 0">${esc(r.conclusion)}</div><b>Работы:</b><ol>` + c.operations.map(o => `<li>${esc(o.text)} <span class="chip" data-doc="${o.doc_id}" data-page="${o.page}" data-q="${esc(o.quote)}">${esc(o.document.slice(0, 22))}, ${t("page")} ${o.page}</span></li>`).join("") + "</ol>";
    if (c.spare_parts.length) h += `<b>Запчасти:</b><ul>` + c.spare_parts.map(o => `<li>${esc(o.text)} <span class="chip" data-doc="${o.doc_id}" data-page="${o.page}" data-q="${esc(o.quote)}">${t("page")} ${o.page}</span></li>`).join("") + "</ul>";
    else h += `<div class="muted">Запчасти: не требуются по найденной процедуре</div>`;
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
async function loadVehicles() {
  const r = await api("/api/vehicles");
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
    `<button id="btnHist" class="expand">🗂 ${t("history")}</button><div id="histBox" class="hidden"></div>`;
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
  if (!VID) return alert(t("choose_vehicle"));
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
    try { const r = await api("/api/stt", {method: "POST", body: fd}); if (r.text) send(r.text); }
    catch (e) { if (e.status === 503) browserStt(); else addMsg("bot", esc(e.message)); }
  };
  rec.start(); $("#mic").classList.add("rec"); $("#txt").placeholder = t("listening");
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
  const r = new SR(); r.lang = SPEECH_LANG[ME.lang] || "ru-RU"; r.interimResults = false;
  r.onresult = e => send(e.results[0][0].transcript); r.start();
}
$("#mic").onclick = startRec;

// ---------------------------------------------------------------- voice out
function plain(text) { return String(text).replace(/\[[^\]]*\b(S|X)\d+\b[^\]]*\]/g, "").replace(/[*#•]/g, "").replace(/\s+/g, " ").slice(0, 900); }
async function speak(text) {
  if (!ME || !A11Y.ttsOn() || !text) return;
  A11Y.stop();
  const p = plain(text), token = ++SPEAK_SEQ;
  const after = () => { if (token === SPEAK_SEQ && $("#s_hands").checked) setTimeout(startRec, 300); };
  if (ME.lang === "ru" && SERVER_TTS) {  // local Silero on the stand; 404 -> browser speech from now on
    try { const r = await fetch("/api/tts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: p})});
      if (token !== SPEAK_SEQ) return;  // a newer message or a tap arrived while synthesizing
      if (r.ok) { const a = new Audio(URL.createObjectURL(await r.blob())); A11Y.audio = a; a.onended = after; a.play(); return; } SERVER_TTS = false; } catch (_) { SERVER_TTS = false; }
  }
  if (!window.speechSynthesis) return;
  const u = new SpeechSynthesisUtterance(p); u.lang = SPEECH_LANG[ME.lang] || "ru-RU"; u.onend = after; speechSynthesis.speak(u);
}
function stopSpeak() { SPEAK_SEQ++; A11Y.stop(); }
$("#chat").addEventListener("click", stopSpeak);  // tap on the chat stops reading

// ---------------------------------------------------------------- settings / login
function applyProfile() {
  langOptions($("#s_lang"), ME.lang);
  document.querySelectorAll("input[name=s_exp]").forEach(r => r.checked = r.value === ME.experience);
  document.querySelectorAll("input[name=s_detail]").forEach(r => r.checked = r.value === (ME.detail || "brief"));
  if (localStorage.getItem("tts") === null) A11Y.setTts(!!ME.voice_out);  // first run: take the old profile flag
  applyI18n();
}
async function saveProfile(patch) { ME = (await api("/api/me", {json: patch})).user; applyProfile(); }
$("#btnSettings").onclick = () => $("#settings").classList.toggle("hidden");
$("#s_lang").onchange = e => saveProfile({lang: e.target.value}).then(loadVehicles);
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
