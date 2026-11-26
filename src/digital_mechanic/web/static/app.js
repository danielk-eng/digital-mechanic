"use strict";
// Mechanic app. Vanilla JS, no build step. Sections: core · i18n · audio player · rendering · vehicle · chat · citation viewer · voice mode · sheets · settings · login.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
let I18N = {}, ME = null, VID = localStorage.getItem("vid") || "", VEH = null, VLIST = [], BUSY = false, LAST_Q = "", HYP = false;
const SPEECH_LANG = {ru: "ru-RU", uz: "uz-UZ", tg: "tg-TJ", kk: "kk-KZ", hi: "hi-IN", vi: "vi-VN"};
const LANG_NAMES = {ru: "Русский", uz: "Oʻzbekcha", tg: "Тоҷикӣ", kk: "Қазақша", hi: "हिन्दी", vi: "Tiếng Việt"};
const t = k => (I18N[(ME && ME.lang) || $("#l_lang")?.value || "ru"] || I18N.ru || {})[k] || (I18N.ru || {})[k] || k;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const ALANG = () => (ME && ME.answer_lang) || (ME && ME.lang) || "ru";

async function api(path, opts = {}) {
  const o = {credentials: "same-origin", ...opts};
  if (o.json) { o.method = o.method || "POST"; o.headers = {"Content-Type": "application/json"}; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch(path, o);
  if (!r.ok) {
    let msg = await r.text();
    try { msg = JSON.parse(msg).detail || msg; } catch (_) {}
    const e = new Error(String(msg).slice(0, 300)); e.status = r.status; throw e;
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}

function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach(el => { if (!el.dataset.iconDone) { el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon, +(el.dataset.size || 22))); el.dataset.iconDone = 1; } });
}
function applyI18n() {
  $$("[data-t]").forEach(el => el.textContent = t(el.dataset.t));
  $$("[data-tp]").forEach(el => el.placeholder = t(el.dataset.tp));
  $$("[data-t-aria]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.tAria)); el.title = t(el.dataset.tAria); });
  document.documentElement.lang = (ME && ME.lang) || "ru";
  if (typeof VID !== "undefined" && !VID && $("#vehTitle")) $("#vehTitle").textContent = t("choose_vehicle");  // vehicle title is not an i18n label
}
function toast(text, ms = 3500) {
  const el = document.createElement("div"); el.className = "toast"; el.textContent = text; document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}
function langOptions(sel, cur, withSame = false) {
  sel.innerHTML = (withSame ? `<option value="">${esc(t("same_as_ui"))}</option>` : "") +
    Object.entries(LANG_NAMES).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${v}</option>`).join("");
}

// ---------------------------------------------------------------- audio player: one at a time, every message has play/stop
const Player = {
  audio: null, btn: null, seq: 0, onend: null,
  stop() {
    this.seq++;
    try { if (this.audio) { this.audio.pause(); this.audio = null; } } catch (_) {}
    try { window.speechSynthesis && speechSynthesis.cancel(); } catch (_) {}
    if (this.btn) { this.btn.innerHTML = icon("play", 18); this.btn.classList.remove("on"); this.btn.setAttribute("aria-label", t("play")); this.btn = null; }
    caption("");
  },
  playing() { return !!this.btn || !!this.audio || (window.speechSynthesis && speechSynthesis.speaking); },
  async play(text, btn = null, {onend = null, onlevel = null} = {}) {
    this.stop();
    const p = plain(text); if (!p) return onend && onend();
    const seq = this.seq;
    if (btn) { this.btn = btn; btn.innerHTML = icon("square", 16); btn.classList.add("on"); btn.setAttribute("aria-label", t("stop")); }
    const done = () => { if (seq !== this.seq) return; if (this.btn) { this.btn.innerHTML = icon("play", 18); this.btn.classList.remove("on"); this.btn = null; } this.audio = null; caption(""); onend && onend(); };
    if (ALANG() === "ru" && SERVER_TTS) {
      try {
        const r = await fetch("/api/tts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: p})});
        if (seq !== this.seq) return;
        if (r.ok) {
          const a = new Audio(URL.createObjectURL(await r.blob())); this.audio = a;
          a.ontimeupdate = () => { if (a.duration) captionFollow(p, a.currentTime / a.duration); };
          a.onended = done; a.onerror = done; await a.play().catch(done); return;
        }
        SERVER_TTS = false;
      } catch (_) { SERVER_TTS = false; }
    }
    if (!window.speechSynthesis) return done();
    const u = new SpeechSynthesisUtterance(p); u.lang = SPEECH_LANG[ALANG()] || "ru-RU";
    u.onboundary = e => captionFollow(p, (e.charIndex || 0) / p.length); u.onend = done; u.onerror = done;
    speechSynthesis.speak(u);
  },
};
let SERVER_TTS = true;
function plain(text) { return String(text || "").replace(/\[[^\]]*\b(S|X)\d+\b[^\]]*\]/g, "").replace(/<[^>]+>/g, " ").replace(/[*#•_]/g, "").replace(/\s+/g, " ").replace(/ ([.,;:!?)])/g, "$1").trim().slice(0, 1200); }
function caption(text, ms = 0) {
  const el = $("#caption"); if (!el) return;
  if (!text || !A11Y.captions() || !$("#voice").classList.contains("hidden")) { el.classList.add("hidden"); return; }
  el.textContent = text; el.classList.remove("hidden");
  clearTimeout(caption.tm); if (ms) caption.tm = setTimeout(() => el.classList.add("hidden"), ms);
}
function captionFollow(p, frac) {
  const ss = p.match(/[^.!?;]+[.!?;]*/g) || [p]; let acc = 0; const pos = frac * p.length;
  for (const x of ss) { acc += x.length; if (acc >= pos) { caption(x.trim()); if (VOICE.on) $("#vReply").dataset.cur = x.trim(); return; } }
}

// ---------------------------------------------------------------- markdown-lite with citation chips
function inline(h, src) {
  h = h.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  return h.replace(/\[([^\]]{1,80})\]/g, (m, grp, off, whole) => {
    const labs = grp.match(/\b(?:S|X)\d+\b/g); if (!labs) return m;
    const ctx = whole.slice(Math.max(0, off - 240), off).replace(/<[^>]+>/g, "").split(/\n|[.!?]\s|•/).filter(x => x.trim().length > 6).pop() || "";
    return labs.map(l => src[l] ? `<button class="cite ${src[l].extra ? "x" : ""}" data-doc="${esc(src[l].doc_id)}" data-page="${src[l].page}" data-q="${esc(ctx.trim().slice(0, 200))}" title="${esc(src[l].document)}">${t("page_short")} ${src[l].page}</button>` : "").join("");
  });
}
function md(text, sources) {
  const src = Object.fromEntries((sources || []).map(s => [s.label, s]));
  const lines = esc(text || "").replace(/\r/g, "").replace(/[ \t]+([.,;:!?)])/g, "$1").replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]\uFE0F?\s?/gu, "").split("\n");
  let out = "", list = null, para = [];
  const flushP = () => { if (para.length) { out += `<p>${inline(para.join("<br>"), src)}</p>`; para = []; } };
  const flushL = () => { if (list) { out += `<${list.tag}>${list.items.map(i => `<li>${inline(i, src)}</li>`).join("")}</${list.tag}>`; list = null; } };
  for (const raw of lines) {
    const l = raw.trim();
    let m;
    if (!l) { flushP(); flushL(); continue; }
    if ((m = l.match(/^#{1,4}\s*(.+)$/))) { flushP(); flushL(); out += `<h3>${inline(m[1], src)}</h3>`; continue; }
    if ((m = l.match(/^(\d{1,2})[.)]\s+(.+)$/))) { flushP(); if (!list || list.tag !== "ol") { flushL(); list = {tag: "ol", items: []}; } list.items.push(m[2]); continue; }
    if ((m = l.match(/^[-•*]\s+(.+)$/))) { flushP(); if (!list || list.tag !== "ul") { flushL(); list = {tag: "ul", items: []}; } list.items.push(m[1]); continue; }
    flushL(); para.push(l);
  }
  flushP(); flushL();
  return out;
}
function sourcesHtml(sources) {
  if (!sources || !sources.length) return "";
  const seen = new Set();
  const rows = sources.filter(s => { const k = s.doc_id + "#" + s.page; if (seen.has(k)) return false; seen.add(k); return true; });
  return `<div class="sources">` + rows.map(s => `<button class="src" data-doc="${esc(s.doc_id)}" data-page="${s.page}">${icon("book-open", 16)}<span>${esc(s.document)}${s.extra ? " *" : ""}</span><span class="pg">${t("page_short")} ${s.page}</span></button>`).join("") + `</div>`;
}
const FACT_ICON = {danger: "triangle-alert", tool: "wrench", torque: "wrench", value: "check"};
function factsHtml(facts) {
  if (!facts || !facts.length) return "";
  return `<div class="keyfacts"><div class="h">${t("key_facts")}</div>` + facts.map(f => `<div class="f ${f.kind}">${icon(FACT_ICON[f.kind] || "check", 16)}<span>${esc(f.text)} ` +
    `<button class="cite" data-doc="${esc(f.doc_id)}" data-page="${f.page}" data-q="${esc(f.text.slice(0, 200))}">${t("page_short")} ${f.page}</button></span></div>`).join("") + `</div>`;
}

// ---------------------------------------------------------------- messages
function addMsg(role, html, {cls = "", speak = null, actions = []} = {}) {
  const d = document.createElement("div"); d.className = `msg ${role}`;
  d.innerHTML = `<div class="b ${cls}">${html}</div>`;
  const acts = [...actions];
  if (speak) acts.unshift({icon: "play", aria: t("play"), cls: "play", onclick: (ev, b) => (Player.btn === b ? Player.stop() : Player.play(speak, b))});
  if (acts.length) {
    const row = document.createElement("div"); row.className = "msg-actions";
    acts.forEach(a => {
      const b = document.createElement("button"); b.className = `act ${a.cls || ""}`; b.innerHTML = (a.icon ? icon(a.icon, 18) : "") + (a.label ? `<span>${esc(a.label)}</span>` : "");
      if (a.aria) { b.setAttribute("aria-label", a.aria); b.title = a.aria; }
      b.onclick = ev => { ev.stopPropagation(); a.onclick(ev, b); }; row.appendChild(b);
    });
    d.appendChild(row);
  }
  $("#chat").appendChild(d);
  requestAnimationFrame(() => d.scrollIntoView({behavior: "smooth", block: "start"}));
  return d;
}
function skeleton() {
  const d = addMsg("bot", `<div class="progress">${icon("loader-circle", 16, "spin")}<span class="ptext">${esc(t("thinking"))}</span></div><div class="line"></div><div class="line"></div><div class="line"></div><div class="line"></div>`, {cls: "skeleton"});
  d.dataset.busy = 1;
  return d;
}
function errorMsg(e) { return addMsg("bot", `<span class="err">${esc(friendlyError(e))}</span>`); }
function friendlyError(e) {
  const m = String(e && e.message || e);
  if (e && e.status === 502) return m.includes("ML") ? t("err_ml") : m.includes("ТОиР") ? t("err_toir") : m;
  if (e && e.status === 401) return t("err_session");
  return m || t("err_generic");
}

function renderTurn(turn, expanded = false) {
  (turn.cards || []).forEach(renderCard);
  if (turn.preface) { /* already part of text */ }
  if (turn.text) {
    const hasSrc = (turn.sources || []).length > 0;
    const und = (turn.understood || []).length ? `<div class="understood">${t("understood")}: ${turn.understood.map(([a, b]) => `${esc(a)} → <b>${esc(b)}</b>`).join(", ")}</div>` : "";
    const actions = [];
    if (hasSrc && !expanded && (ME.detail || "brief") === "brief") actions.push({label: t("expand"), onclick: (ev, b) => { b.remove(); eventTurn("expand", turn.text, null, true); }});
    if (hasSrc && VID) actions.push({icon: "ellipsis", aria: t("more_actions"), onclick: () => turnMenu(turn)});
    addMsg("bot", und + factsHtml(turn.facts) + md(turn.text, turn.sources) + sourcesHtml(turn.sources), {speak: turn.text, actions});
  }
  loadChips();
  return turn;
}
function turnMenu(turn) {
  sheet(t("more_actions"), `<div class="list">` +
    `<button class="item" data-a="alt">${icon("shuffle")}<span class="t"><b>${t("alternatives")}</b></span></button>` +
    `<button class="item" data-a="hyp">${icon("flask-conical")}<span class="t"><b>${t("hypothesis")}</b><span>${t("hypothesis_ph")}</span></span></button>` +
    `<button class="item" data-a="prn">${icon("printer")}<span class="t"><b>${t("print")}</b></span></button></div>`, sh => {
    sh.querySelector("[data-a=alt]").onclick = () => { closeSheet(); eventTurn("alternatives", "", t("alternatives")); };
    sh.querySelector("[data-a=hyp]").onclick = () => { closeSheet(); HYP = true; $("#txt").placeholder = t("hypothesis_ph"); $("#txt").focus(); };
    sh.querySelector("[data-a=prn]").onclick = () => { closeSheet(); printTurn(turn); };
  });
}

// ---------------------------------------------------------------- cards
function spark(trend) {
  const pts = (trend || []).map(p => ({...p, date: p.checked_at && p.checked_at.slice(0, 10)}));
  return tlSpark(pts);
}
const QUALITY = {good: "q_good", medium: "q_medium", bad: "q_bad"};
const LIGHT_ICON = {go: "circle-check", watch: "circle-check", service: "triangle-alert", stop: "triangle-alert", repeat: "rotate-ccw"};
function renderCard(c) {
  if (c.kind === "verdict") {
    const ch = c.check;
    const head = c.status === "anomaly" ? `${ch.anomaly_type} · ${esc(ch.anomaly_title)}` : c.status === "normal" ? t("verdict_normal") : t("verdict_failed");
    let h = `<div class="vh"><div class="vi">${icon(LIGHT_ICON[c.light] || "activity", 22)}</div><div><div class="vt">${head}</div>` +
      (c.light_text && c.status === "anomaly" ? `<div class="vs">${esc(c.light_text)}</div>` : "") + `</div></div>`;
    h += `<div class="kv"><span>${t("session")}</span><span class="mono">${esc(ch.session_id)}, ${esc((ch.checked_at || "").slice(0, 16).replace("T", " "))}</span>`;
    if (ch.anomaly_score != null) h += `<span>${t("score")}</span><span class="mono">${ch.anomaly_score.toFixed(2)}</span>`;
    if (ch.confidence != null) h += `<span>${t("confidence")}</span><span class="mono">${Math.round(ch.confidence * 100)}%</span>`;
    h += `<span>${t("quality")}</span><span>${esc(t(QUALITY[ch.signal_quality] || ch.signal_quality))}</span>`;
    if (ch.description) h += `<span>${t("ml_says")}</span><span>${esc(ch.description)}</span>`;
    h += `</div>`;
    if (c.doubt && c.doubt.length) h += `<p class="warn-text small" style="margin-top:12px">${t("doubt")}: ${c.doubt.map(esc).join("; ")}</p>`;
    if (c.urgency_basis && c.urgency_basis.length) h += `<details class="more"><summary>${icon("chevron-right", 16)}${t("why_priority")} ${esc(t("urg_" + c.urgency))}</summary><div class="small muted">${c.urgency_basis.map(esc).join("<br>")}</div></details>`;
    if (c.trend) h += `<div class="spark">${spark(c.trend)}</div>`;
    const say = c.status === "anomaly" ? `${t("status_anomaly")}: ${ch.anomaly_title}. ${c.light_text || ""}` : head;
    const vd = addMsg("bot", h, {cls: `verdict ${c.light}`, speak: say});
    if (c.status === "anomaly" && ch.anomaly_type) loadProven(vd.querySelector(".b"), ch.anomaly_type);
  } else if (c.kind === "ticket_draft") {
    const r = c.request;
    let h = `<div class="card-title">${icon("file-text", 20)}${t("create_ticket")}<span class="pill ${r.urgency === "high" ? "quarantine" : r.urgency === "medium" ? "attention" : "ready"}" style="margin-left:auto">${esc(t("urg_" + r.urgency))}</span></div>`;
    h += `<p>${esc(r.conclusion)}</p><div class="label">${t("works")}</div><ol class="ops">` + c.operations.map((o, i) => `<li><span class="n">${i + 1}</span><span>${esc(o.text)} <button class="cite" data-doc="${esc(o.doc_id)}" data-page="${o.page}" data-q="${esc(o.quote)}">${t("page_short")} ${o.page}</button></span></li>`).join("") + "</ol>";
    h += c.spare_parts.length ? `<div class="label">${t("parts")}</div><ul>` + c.spare_parts.map(o => `<li>${esc(o.text)} <button class="cite" data-doc="${esc(o.doc_id)}" data-page="${o.page}" data-q="${esc(o.quote)}">${t("page_short")} ${o.page}</button></li>`).join("") + "</ul>"
      : `<p class="small muted">${t("parts_none")}</p>`;
    if (c.labor) { const known = c.labor.filter(l => l.norm);
      h += `<p class="small muted">${t("labor")}: ` + (known.length ? known.map(l => `${esc(l.norm)} <button class="cite" data-doc="${esc(l.doc_id)}" data-page="${l.page}">${t("page_short")} ${l.page}</button>`).join("; ") : t("labor_na")) + `</p>`; }
    h += `<details class="more"><summary>${icon("chevron-right", 16)}${t("json_toir")}</summary><pre class="json">${esc(JSON.stringify(r, null, 1))}</pre></details><div class="card-actions"></div>`;
    const d = addMsg("bot", h);
    const row = d.querySelector(".card-actions");
    const ok = document.createElement("button"); ok.className = "btn primary"; ok.textContent = c.requires_inspection ? t("inspection_ok") : t("confirm_send");
    ok.onclick = () => confirmTicket(c.draft_id, true, row);
    const no = document.createElement("button"); no.className = "btn ghost"; no.textContent = c.requires_inspection ? t("inspection_no") : t("cancel");
    no.onclick = () => c.requires_inspection ? confirmTicket(c.draft_id, false, row) : (row.innerHTML = `<span class="muted small">${t("cancelled")}</span>`);
    row.append(ok, no);
  } else if (c.kind === "ticket_created") {
    addMsg("bot", `<div class="vh"><div class="vi">${icon("circle-check", 22)}</div><div><div class="vt">${t("ticket_created")}: <span class="mono">${esc(c.ticket_id)}</span></div><div class="vs">${esc(c.request.conclusion)}</div></div></div>`, {cls: "verdict go", speak: `${t("ticket_created")} ${c.ticket_id}`});
  } else if (c.kind === "repair") {
    const n = c.steps.length, cur = c.current;
    let h = `<div class="card-title">${icon("wrench", 20)}${esc(c.title)}</div><div class="small muted">${c.status === "done" ? t("repair_done") : `${t("step")} ${Math.min(cur + 1, n)} ${t("of")} ${n}`}</div><ol class="ops">` +
      c.steps.map((s, i) => `<li class="${s.done ? "done" : i === cur ? "cur" : ""}"><span class="n">${s.done ? icon("check", 14) : i + 1}</span><span>${esc(s.text)} <button class="cite" data-doc="${esc(s.doc_id)}" data-page="${s.page}" data-q="${esc(s.quote)}">${t("page_short")} ${s.page}</button></span></li>`).join("") + "</ol>";
    const curStep = c.steps[cur];
    const d = addMsg("bot", h + (c.status === "done" ? "" : `<div class="card-actions"></div>`), {speak: curStep && c.status !== "done" ? `${t("step")} ${cur + 1}. ${curStep.text}` : t("repair_done")});
    if (c.status !== "done") {
      const row = d.querySelector(".card-actions");
      const b1 = document.createElement("button"); b1.className = "btn primary"; b1.textContent = t("next_step"); b1.onclick = () => repairAction("done");
      const b2 = document.createElement("button"); b2.className = "btn ghost"; b2.textContent = t("step_back"); b2.style.flex = "0 0 auto"; b2.onclick = () => repairAction("back");
      row.append(b1, b2);
    }
  }
}
async function loadProven(box, code) {
  try {
    const r = await api(`/api/proven?vehicle_id=${encodeURIComponent(VID)}&code=${encodeURIComponent(code)}`);
    if (!r.fixes.length) return;
    const ver = f => f.verified === true ? `<span class="ok-text">${t("fix_verified")}</span>` : f.verified === false ? `<span class="warn-text">${t("fix_not_verified")}</span>` : "";
    box.insertAdjacentHTML("beforeend", `<details class="more proven"><summary>${icon("chevron-right", 16)}${t("proven_fixes")}: ${r.fixes.length}</summary>` +
      r.fixes.map(f => `<div class="fix"><div><b class="mono">${esc(f.vehicle_id)}</b> <span class="muted">${esc(f.date || "")}${f.ticket_id ? ", " + esc(f.ticket_id) : ""}</span></div><div class="small">${ver(f)}${f.source === "demo" ? ` <span class="muted">(${t("demo_hist")})</span>` : ""}</div><ol>` +
        f.steps.map(st => `<li>${esc(st.text)}${st.page ? ` <button class="cite" data-doc="${esc(st.doc_id)}" data-page="${st.page}" data-q="${esc(st.text)}">${t("page_short")} ${st.page}</button>` : ""}</li>`).join("") + `</ol></div>`).join("") + `</details>`);
  } catch (_) {}
}
async function confirmTicket(draftId, ok, row) {
  row.innerHTML = `<span class="muted small">${icon("loader-circle", 16, "spin")}</span>`;
  try {
    const r = await api("/api/ticket/confirm", {json: {vehicle_id: VID, draft_id: draftId, inspection_ok: ok}});
    row.innerHTML = "";
    if (r.card) renderCard(r.card); else addMsg("bot", md(r.text || r.error || ""), {speak: r.text});
    refreshVehicle();
  } catch (e) { row.innerHTML = `<span class="err small">${esc(friendlyError(e))}</span>`; }
}
async function repairAction(a) {
  try { const r = await api("/api/repair/action", {json: {vehicle_id: VID, action: a}}); if (r.card) { renderCard(r.card); loadChips(); } }
  catch (e) { errorMsg(e); }
}

// ---------------------------------------------------------------- chips
let CHIP_SEQ = 0;
async function loadChips() {
  const q = $("#quick"); if (!q) return;
  if (!ME || !VID) { q.innerHTML = ""; return; }
  const seq = ++CHIP_SEQ;
  try {
    const r = await api(`/api/suggest?vehicle_id=${encodeURIComponent(VID)}`);
    if (seq !== CHIP_SEQ) return;
    q.innerHTML = "";
    (r.chips || []).slice(0, 6).forEach(ch => {
      const b = document.createElement("button"); b.textContent = ch.text;
      if (ch.page) b.title = `${ch.doc}, ${t("page_short")} ${ch.page}`;
      b.onclick = ch.kind === "triage" ? () => eventTurn("triage", "", ch.text) : ch.kind === "code" ? () => { $("#txt").value = "F0"; autosize(); $("#txt").focus(); } : () => send(ch.text);
      q.appendChild(b);
    });
    layout();
  } catch (_) {}
}

// ---------------------------------------------------------------- vehicle
const LAT = {"А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X"};
const vnorm = s => (s || "").toUpperCase().replace(/[АВЕКМНОРСТУХ]/g, c => LAT[c]).replace(/[^A-ZА-ЯЁ0-9]/g, "");
function findVehicles(q) {
  const n = vnorm(q); if (n.length < 2) return [];
  const dig = /^\d+$/.test(n);
  const exact = VLIST.filter(v => vnorm(v.vehicle_id) === n || vnorm(v.garage_no) === n || (dig && vnorm(v.garage_no).replace(/\D/g, "") === n));
  if (exact.length) return exact;
  return VLIST.filter(v => vnorm(v.vehicle_id).includes(n) || vnorm(v.garage_no).includes(n) || vnorm(v.model).includes(n)).slice(0, 8);
}
function plateInText(text) {
  const m = vnorm(text).match(/[ABEKMHOPCTYX]\d{3}[ABEKMHOPCTYX]{2}\d{2,3}/);
  if (m) { const f = findVehicles(m[0]); if (f.length === 1) return f[0]; }
  const g = (text || "").match(/г(?:ар(?:аж(?:ный)?)?)?\.?\s*(?:№|n|номер)?\s*-?\s*(\d{2,4})/i);
  if (g) { const f = VLIST.filter(v => vnorm(v.garage_no).replace(/\D/g, "") === g[1]); if (f.length === 1) return f[0]; }
  return null;
}
const STATUS_ORDER = {quarantine: 0, attention: 1, ready: 2};
const vStatus = v => v.in_repair ? "repair" : v.status;
const vStatusText = v => v.in_repair ? t("in_repair") : t(v.status);
function vehicleItem(v) {
  return `<button class="item ${v.vehicle_id === VID ? "sel" : ""}" data-v="${esc(v.vehicle_id)}"><span class="dot ${vStatus(v)}"></span><span class="t"><b class="mono">${esc(v.vehicle_id)}</b><span>${esc(v.model || "")}, ${esc(v.engine_model)}, ${esc(v.garage_no || "")}</span></span><span class="small muted">${esc(vStatusText(v))}</span></button>`;
}
function vehiclePicker(box, onPick) {
  box.innerHTML = `<div class="field" style="margin-bottom:8px"><label for="vfind" class="label">${t("find_vehicle")}</label><input id="vfind" class="input" type="search" autocomplete="off" placeholder="${esc(t("find_vehicle_ph"))}"></div><div class="list" id="vlist"></div>`;
  const draw = () => {
    const q = box.querySelector("#vfind").value;
    const list = q.trim() ? findVehicles(q) : VLIST.slice().sort((a, b) => (STATUS_ORDER[a.status] - STATUS_ORDER[b.status]) || a.vehicle_id.localeCompare(b.vehicle_id));
    box.querySelector("#vlist").innerHTML = list.length ? list.map(vehicleItem).join("") : `<p class="muted">${t("not_found")}</p>`;
    box.querySelectorAll("[data-v]").forEach(b => b.onclick = () => onPick(b.dataset.v));
    if (q.trim() && list.length === 1 && vnorm(q).length >= 6) onPick(list[0].vehicle_id);
  };
  box.querySelector("#vfind").oninput = draw; draw();
}
function showEmpty() {
  $("#empty").classList.remove("hidden"); $("#vehInfo").classList.add("hidden"); $("#composer").classList.add("hidden"); $("#btnMenu").classList.add("hidden");
  $("#empty").innerHTML = `<div class="empty">${icon("truck", 40)}<h2>${t("choose_vehicle")}</h2><p>${t("choose_vehicle_text")}</p></div><div id="pick"></div>`;
  vehiclePicker($("#pick"), vid => selectVehicle(vid));
  $("#vehTitle").textContent = t("choose_vehicle"); $("#vehSub").textContent = ""; $("#vehDot").className = "dot";
}
async function loadVehicles() {
  const r = await api("/api/vehicles"); VLIST = r.vehicles;
  if (VID && VLIST.some(v => v.vehicle_id === VID)) await selectVehicle(VID); else { VID = ""; showEmpty(); }
}
async function selectVehicle(vid) {
  closeSheet(); Player.stop();
  VID = vid; localStorage.setItem("vid", vid); $("#chat").innerHTML = "";
  if (!vid) return showEmpty();
  $("#empty").classList.add("hidden"); $("#empty").innerHTML = ""; $("#composer").classList.remove("hidden"); $("#btnMenu").classList.remove("hidden");
  const lv = VLIST.find(v => v.vehicle_id === vid) || {};
  $("#vehTitle").textContent = vid; $("#vehSub").textContent = `${lv.model || ""} · ${lv.engine_model || ""}`; $("#vehDot").className = `dot ${vStatus(lv)}`;
  $("#vehInfo").classList.remove("hidden"); $("#vehInfo").innerHTML = `<div class="skeleton"><div class="line"></div><div class="line"></div><div class="line"></div></div>`;
  layout(); loadChips();
  let r;
  try { r = await api(`/api/vehicle/${encodeURIComponent(vid)}`); } catch (e) { $("#vehInfo").innerHTML = `<span class="err">${esc(friendlyError(e))}</span>`; return; }
  if (VID !== vid) return;
  VEH = r;
  drawVehicle(r, lv);
  for (const m of r.messages) {
    (m.meta.cards || []).forEach(renderCard);
    if (m.text) m.role === "user" ? addMsg("user", esc(m.text)) : addMsg("bot", md(m.text, m.meta.sources) + sourcesHtml(m.meta.sources), {speak: m.text});
  }
  if (r.draft && !r.messages.some(m => (m.meta.cards || []).some(c => c.kind === "ticket_draft"))) renderCard(r.draft);
  if (!r.messages.length && r.repair) renderCard({...r.repair, kind: "repair"});
  window.scrollTo({top: 0});
}
function drawVehicle(r, lv) {
  const v = r.vehicle;
  const model = v.model && v.model.startsWith(v.make) ? v.model : `${v.make} ${v.model || ""}`;
  $("#vehInfo").innerHTML = `<div class="top"><div><h2>${esc(v.vehicle_id)}</h2><div class="meta">${esc(model)}, ${esc(v.vehicle_type)}</div></div><span class="pill ${vStatus(lv)}">${esc(vStatusText(lv))}</span></div>` +
    `<div class="facts-list"><div class="r">${icon("settings", 16)}<span><b>${esc(v.engine_model)}</b>, ${esc(v.fuel)}, ${t("garage")} ${esc(v.garage_no)}</span></div>` +
    `<div class="r">${icon("book-open", 16)}<span>${t("scope_only")} ${esc(v.engine_model)}: ${r.manuals.map(m => esc(m.title)).join(", ")}</span></div></div>` +
    `<div id="arrBox"></div>` +
    `<div class="diag-row"><div class="sess"><label class="label" for="sess">${t("session")}</label><select id="sess" class="input">` +
    r.sessions.slice().reverse().map((s, i) => `<option value="${esc(s.session_id)}">${esc(s.session_id)}, ${esc((s.checked_at || "").slice(0, 10))}${i === 0 ? ", " + t("latest") : ""}</option>`).join("") +
    `</select></div><button class="btn primary" id="btnDiag">${icon("activity", 20)}<span>${t("get_result")}</span></button></div>`;
  $("#btnDiag").onclick = diagnose;
  api(`/api/history/${encodeURIComponent(v.vehicle_id)}`).then(tl => { if (VID === v.vehicle_id && $("#arrBox")) $("#arrBox").innerHTML = arrivalCard(tl, t); }).catch(() => {});
}
async function refreshVehicle() {
  try { const r = await api("/api/vehicles"); VLIST = r.vehicles; const lv = VLIST.find(v => v.vehicle_id === VID); if (lv) { $("#vehDot").className = `dot ${vStatus(lv)}`; const p = $("#vehInfo .pill"); if (p) { p.className = `pill ${vStatus(lv)}`; p.textContent = vStatusText(lv); } } } catch (_) {}
}
function vehicleMenu() {
  sheet(VID, `<div class="list">` +
    `<button class="item" data-a="hist">${icon("history")}<span class="t"><b>${t("history")}</b></span></button>` +
    `<button class="item" data-a="sum">${icon("file-text")}<span class="t"><b>${t("summary")}</b><span>${t("summary_sub")}</span></span></button>` +
    `<button class="item" data-a="hand">${icon("forward")}<span class="t"><b>${t("handoff")}</b><span>${t("handoff_sub")}</span></span></button>` +
    `<button class="item" data-a="insp">${icon("clipboard-check")}<span class="t"><b>${t("inspection")}</b><span>${t("inspection_sub")}</span></span></button>` +
    `<div class="sep"></div><button class="item" data-a="other">${icon("truck")}<span class="t"><b>${t("other_vehicle")}</b></span></button></div>`, sh => {
    sh.querySelector("[data-a=hist]").onclick = () => showHistory();
    sh.querySelector("[data-a=sum]").onclick = () => { closeSheet(); doSummary(); };
    sh.querySelector("[data-a=hand]").onclick = () => handoffSheet();
    sh.querySelector("[data-a=insp]").onclick = () => { closeSheet(); doInspection(); };
    sh.querySelector("[data-a=other]").onclick = () => pickVehicleSheet();
  });
}
function pickVehicleSheet() { sheet(t("vehicle"), `<div id="pick2"></div>`, sh => { vehiclePicker(sh.querySelector("#pick2"), vid => selectVehicle(vid)); setTimeout(() => sh.querySelector("#vfind").focus(), 50); }); }
async function showHistory() {
  sheet(t("history"), `<div class="skeleton"><div class="line"></div><div class="line"></div><div class="line"></div></div>`, async sh => {
    try { sh.querySelector(".sb").innerHTML = renderTimeline(await api(`/api/history/${encodeURIComponent(VID)}`)); }
    catch (e) { sh.querySelector(".sb").innerHTML = `<span class="err">${esc(friendlyError(e))}</span>`; }
  });
}
async function doSummary() {
  const th = skeleton();
  try { const s = await api("/api/summary", {json: {vehicle_id: VID}}); th.remove(); addMsg("bot", `<div class="card-title">${icon("file-text", 20)}${t("summary")}</div>` + md(s.text) + sourcesHtml(s.pages), {speak: s.text}); }
  catch (e) { th.remove(); errorMsg(e); }
}
function handoffSheet() {
  sheet(t("handoff"), `<div class="field"><label for="hNote" class="label">${t("handoff_note")}</label><textarea id="hNote" class="input" rows="3" style="padding:12px 16px"></textarea></div><button class="btn primary block" id="hGo">${t("handoff_send")}</button>`, sh => {
    sh.querySelector("#hGo").onclick = async () => {
      const note = sh.querySelector("#hNote").value; closeSheet();
      const th = skeleton();
      try { const s = await api("/api/handoff", {json: {vehicle_id: VID, note}}); th.remove();
        addMsg("bot", `<div class="card-title">${icon("forward", 20)}${t("handoff_done")} №${s.id}</div>` + md(s.summary) + sourcesHtml(s.pages), {speak: s.summary}); }
      catch (e) { th.remove(); errorMsg(e); }
    };
    setTimeout(() => sh.querySelector("#hNote").focus(), 50);
  });
}
async function doInspection() {
  const th = skeleton();
  try {
    const r = await api("/api/inspection/draft", {json: {vehicle_id: VID}}); th.remove();
    if (!r.items.length) return addMsg("bot", `<p>${esc(r.note)}</p>`);
    const sev = {"норма": "ready", "внимание": "attention", "дефект": "quarantine"};
    const d = addMsg("bot", `<div class="card-title">${icon("clipboard-check", 20)}${t("inspection")}</div><p class="small muted">${t("insp_review")}</p>` + r.items.map((it, i) =>
      `<label class="insp"><input type="checkbox" data-i="${i}" checked style="width:22px;height:22px;accent-color:var(--accent)"><span style="flex:1"><b contenteditable="true" data-f="component">${esc(it.component)}</b>: <span contenteditable="true" data-f="finding">${esc(it.finding)}</span>` +
      `<span class="small muted" style="display:block">${esc(it.source)}</span></span><span class="pill ${sev[it.severity] || "attention"}">${esc(it.severity)}</span>` +
      (it.page ? `<button class="cite" data-doc="${esc(it.doc_id)}" data-page="${it.page}">${t("page_short")} ${it.page}</button>` : "") + `</label>`).join("") +
      `<div class="card-actions"><button class="btn primary" id="inspOk">${t("insp_confirm")}</button></div>`);
    d.querySelector("#inspOk").onclick = async () => {
      const items = r.items.map((it, i) => { const row = d.querySelectorAll(".insp")[i];
        return {...it, component: row.querySelector('[data-f="component"]').textContent.trim(), finding: row.querySelector('[data-f="finding"]').textContent.trim(), confirmed: row.querySelector("input").checked}; });
      try { const s = await api("/api/inspection/confirm", {json: {vehicle_id: VID, items}}); d.querySelector(".card-actions").innerHTML = `<span class="ok-text">${t("insp_saved")} №${s.id}</span>`; }
      catch (e) { toast(friendlyError(e)); }
    };
  } catch (e) { th.remove(); errorMsg(e); }
}

// ---------------------------------------------------------------- chat
async function diagnose() {
  Player.stop();
  if (BUSY) return; BUSY = true; $("#btnDiag").disabled = true;
  try {
    const r = await api("/api/diagnose", {json: {vehicle_id: VID, session_id: $("#sess").value || null}});
    renderCard(r.card); loadChips(); refreshVehicle();
    BUSY = false;
    await ask("", "explain_diagnosis");
  } catch (e) { errorMsg(e); }
  finally { BUSY = false; const b = $("#btnDiag"); if (b) b.disabled = false; }
}
async function ask(text, event = null) {
  if (BUSY) return null; BUSY = true;
  const th = skeleton(), t0 = Date.now();
  const tick = setInterval(async () => {
    const sec = Math.round((Date.now() - t0) / 1000);
    let steps = [], draft = "";
    try { const p = await api(`/api/progress?vehicle_id=${encodeURIComponent(VID || "")}`); steps = p.steps || []; draft = p.draft || ""; } catch (_) {}
    if (!th.isConnected) return;
    const el = th.querySelector(".ptext"); if (el) { if (steps.length) el.dataset.step = steps[steps.length - 1]; el.textContent = `${draft ? t("writing") : (el.dataset.step || t("thinking"))} · ${sec} ${t("sec")}`; }
    if (draft && draft !== th.dataset.draft) {  // live lines already checked against the manual (numbers), replaced by the final answer
      th.dataset.draft = draft; th.classList.add("has-draft");
      let box = th.querySelector(".draft"); if (!box) { box = document.createElement("div"); box.className = "draft"; th.querySelector(".progress").after(box); }
      box.innerHTML = md(draft, []);
    }
    if (VOICE.on && steps.length) $("#vHint").textContent = steps[steps.length - 1];
  }, 1000);
  try { const r = await api("/api/chat", {json: {vehicle_id: VID || null, text, event}}); th.remove(); return renderTurn(r, event === "expand"); }
  catch (e) { th.remove(); errorMsg(e); return null; }
  finally { clearInterval(tick); BUSY = false; }
}
async function eventTurn(event, text, shown, expanded = false) {
  Player.stop();
  if (shown) addMsg("user", esc(shown));
  return ask(text, event);
}
async function send(text) {
  Player.stop();
  text = (text ?? $("#txt").value).trim();
  if (!text) return null;
  if (!VID) {
    const v = plateInText(text); if (!v) { toast(t("choose_vehicle")); return null; }
    await selectVehicle(v.vehicle_id);
  }
  LAST_Q = text; $("#txt").value = ""; autosize();
  if (HYP) { HYP = false; $("#txt").placeholder = t("ask_placeholder"); return eventTurn("hypothesis", text, text); }
  addMsg("user", esc(text));
  return ask(text);
}
function autosize() {
  const ta = $("#txt"); ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  const has = !!ta.value.trim(); $("#send").classList.toggle("hidden", !has); $("#mic").classList.toggle("hidden", has);
  layout();
}
function layout() { const c = $("#composer"); if (c) document.documentElement.style.setProperty("--composer-h", (c.classList.contains("hidden") ? 0 : c.offsetHeight) + "px"); }

// ---------------------------------------------------------------- photo
async function onPhoto(e) {
  const f = e.target.files[0]; if (!f) return;
  const url = URL.createObjectURL(f), note = $("#txt").value.trim();
  addMsg("user", `<img src="${url}" alt="" style="max-width:240px;width:100%;border-radius:12px;display:block">${note ? `<div style="margin-top:8px">${esc(note)}</div>` : ""}`);
  const fd = new FormData(); fd.append("file", f); fd.append("vehicle_id", VID); fd.append("text", note); $("#txt").value = ""; autosize();
  const th = skeleton();
  try {
    const r = await api("/api/photo", {method: "POST", body: fd}); th.remove();
    if (r.description) addMsg("bot", `<p class="muted">${esc(r.description)}</p>`);
    if (r.plate && r.plate !== VID) {
      const d = addMsg("bot", `<p>${t("vehicle")}: <b class="mono">${esc(r.plate)}</b>?</p><div class="card-actions"><button class="btn primary">${t("open_vehicle")}</button></div>`);
      d.querySelector("button").onclick = () => selectVehicle(r.plate);
    }
    if (r.turn) renderTurn(r.turn);
  } catch (err) { th.remove(); errorMsg(err); }
  e.target.value = "";
}

// ---------------------------------------------------------------- citation viewer: extract → full section (collapsed) → page links and image
function hl(txt, q) {
  const words = [...new Set((q || "").toLowerCase().match(/[\p{L}\d]{5,}/gu) || [])].slice(0, 12);
  if (!words.length) return txt;
  const re = new RegExp(`(${words.map(w => w.slice(0, Math.max(5, w.length - 2)).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})[\\p{L}]*`, "giu");
  return txt.replace(re, m => `<mark>${m}</mark>`);
}
async function openCitation(doc, page, q) {
  Player.stop();
  $("#viewer").classList.remove("hidden"); document.body.style.overflow = "hidden";
  $("#mTitle").textContent = `${t("page")} ${page}`; $("#mSub").textContent = ""; $("#mPdf").classList.add("hidden");
  $("#mBody").innerHTML = `<div class="extract skeleton"><div class="line"></div><div class="line"></div><div class="line"></div></div>`;
  try {
    const s = await api(`/api/section/${encodeURIComponent(doc)}/${page}?q=${encodeURIComponent(q || LAST_Q)}`);
    $("#mTitle").textContent = s.label || `${t("page")} ${page}`; $("#mSub").textContent = s.full_title;
    if (s.pdf) { $("#mPdf").href = `/manuals/${encodeURIComponent(doc)}.pdf#page=${page}`; $("#mPdf").classList.remove("hidden"); }
    const pages = s.pages.map(p => p.page);
    const link = n => s.pdf ? `<a target="_blank" href="/manuals/${encodeURIComponent(doc)}.pdf#page=${n}" class="${n === page ? "on" : ""}">${t("page_short")} ${n} ${icon("external-link", 14)}</a>`
      : `<button data-img="${n}" class="${n === page ? "on" : ""}">${t("page_short")} ${n}</button>`;
    $("#mBody").innerHTML = (s.extra ? `<p class="small muted">* ${t("extra_src")}</p>` : "") +
      `<div class="extract" id="mExtract"><div class="h">${t("extract")}</div><div class="skeleton"><div class="line"></div><div class="line"></div><div class="line"></div></div></div>` +
      `<div class="label">${t("pages")}</div><div class="pages">${pages.map(link).join("")}</div>` +
      `<details class="sec"><summary>${icon("chevron-right", 18)}<span>${t("full_section")}, ${t("page_short")} ${esc(s.span)}</span></summary><div class="in">` +
      s.pages.map(p => `<div class="pagehead">${t("page")} ${p.page}</div><div class="pagetext">${hl(esc(p.text), q)}</div>`).join("") + `</div></details>` +
      (s.has_image ? `<details class="sec" id="mImgBox"><summary>${icon("chevron-right", 18)}<span id="mImgT">${t("page_image")} ${page}</span></summary><div class="in"><img id="mImg" class="pageimg" loading="lazy" src="/page/${encodeURIComponent(doc)}/${page}.png" alt="${esc(t("page"))} ${page}"></div></details>` : "");
    $("#mBody").querySelectorAll("[data-img]").forEach(c => c.onclick = () => { const n = c.dataset.img;
      if ($("#mImg")) { $("#mImg").src = `/page/${encodeURIComponent(doc)}/${n}.png`; $("#mImgBox").open = true; $("#mImgT").textContent = `${t("page_image")} ${n}`; } });
    api("/api/extract", {json: {doc_id: doc, page, q, question: LAST_Q}}).then(x => {
      const box = $("#mExtract"); if (!box) return;
      box.innerHTML = `<div class="h">${t("extract")}</div>` + (x.ok ? md(x.text) : `<p class="muted">${t("extract_na")}</p>`);
    }).catch(() => { const box = $("#mExtract"); if (box) box.innerHTML = `<div class="h">${t("extract")}</div><p class="muted">${t("extract_na")}</p>`; });
  } catch (err) { $("#mBody").innerHTML = `<p class="err">${esc(friendlyError(err))}</p>`; }
}
function closeViewer() { $("#viewer").classList.add("hidden"); document.body.style.overflow = ""; }

// ---------------------------------------------------------------- print / PDF of the current procedure
async function printTurn(turn) {
  const w = window.open("", "_blank"); if (!w) return toast(t("popup_blocked"));
  w.document.write(`<p style="font-family:sans-serif">${esc(t("thinking"))}</p>`);
  const veh = VEH ? `${VEH.vehicle.vehicle_id}, ${VEH.vehicle.model || ""}, ${VEH.vehicle.engine_model}` : "";
  const labor = (turn.cards || []).flatMap(c => c.labor || []);
  const lab = labor.length ? `<p>${t("labor")}: ` + (labor.some(l => l.norm) ? labor.filter(l => l.norm).map(l => `${esc(l.norm)} (${t("page_short")} ${l.page})`).join("; ") : t("labor_na")) + `</p>` : "";
  const seen = new Set(), pages = [];
  for (const s of (turn.sources || [])) { const k = s.doc_id + "#" + s.page; if (seen.has(k) || pages.length >= 6) continue; seen.add(k);
    let info = {}; try { info = await api(`/api/page/${encodeURIComponent(s.doc_id)}/${s.page}`); } catch (e) {}
    pages.push(`<section class="pg"><h3>${esc(info.full_title || s.document)}, ${t("page_short")} ${s.page}</h3>` +
      (info.has_image ? `<img src="/page/${encodeURIComponent(s.doc_id)}/${s.page}.png">` : `<pre>${esc((info.text || "").slice(0, 4000))}</pre>`) + `</section>`); }
  const css = `body{font:12pt/1.5 Inter,system-ui,sans-serif;color:#111;margin:1.6cm}h1{font-size:16pt;margin:0 0 4pt}h3{font-size:11pt;margin:0 0 6pt}
    .cite{font-size:.8em;color:#444;border:0;background:none;padding:0}.cite:before{content:"["}.cite:after{content:"]"}.keyfacts{border:1px solid #bbb;border-radius:6px;padding:6pt 10pt;margin:10pt 0}
    .keyfacts .h{font-weight:600}.keyfacts .f{margin:4pt 0}.ic{display:none}.sources{display:none}img{max-width:100%;border:1px solid #ccc}pre{white-space:pre-wrap;font-size:9.5pt;border:1px solid #ccc;padding:8pt}
    .pg{page-break-before:always}.muted{color:#555;font-size:.9em}.noprint{font:600 11pt Inter,system-ui,sans-serif;background:#2f6fe0;color:#fff;border:0;border-radius:8px;padding:10px 18px;cursor:pointer;margin-bottom:12pt}@media print{.noprint{display:none}}`;
  w.document.open();
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(t("app_title"))}, ${esc(veh)}</title><link rel="stylesheet" href="/static/fonts/fonts.css"><style>${css}</style></head><body>
    <p><button class="noprint" onclick="print()">${t("print")}</button></p><h1>${esc(t("app_title"))}</h1><p class="muted">${esc(veh)}, ${new Date().toLocaleString("ru-RU", {dateStyle: "short", timeStyle: "short"})}, ${esc((ME && ME.name) || "")}</p>
    ${factsHtml(turn.facts)}<div>${md(turn.text, turn.sources)}</div>${lab}<p class="muted">${t("print_note")}</p>${pages.join("")}</body></html>`);
  w.document.close();
  await Promise.all([...w.document.images].map(i => i.complete ? 1 : new Promise(r => { i.onload = i.onerror = r; })));
  w.focus(); w.print();
}

// ---------------------------------------------------------------- voice dialog mode: listen → VAD → STT → answer → speak → listen again; barge-in stops speech
const VOICE = {on: false, state: "idle", stream: null, ctx: null, an: null, rec: null, chunks: [], raf: 0, heard: false, quietMs: 0, speechMs: 0, t0: 0, last: 0, floor: 4};
const V_ICON = {listening: "mic", transcribing: "loader-circle", thinking: "loader-circle", speaking: "square", idle: "mic"};
function vSet(state, hint = "") {
  VOICE.state = state;
  $("#vOrb").dataset.state = state;
  $("#vOrbIc").innerHTML = icon(V_ICON[state], 56, state === "thinking" || state === "transcribing" ? "spin" : "");
  $("#vState").textContent = t("v_" + state);
  $("#vHint").textContent = hint || t("vh_" + state);
}
async function voiceOpen() {
  if (!VID) return toast(t("choose_vehicle"));
  Player.stop();
  $("#voice").classList.remove("hidden"); $("#vSaid").textContent = ""; $("#vReply").textContent = ""; VOICE.on = true;
  try {
    VOICE.stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
  } catch (e) { vSet("idle", t("v_no_mic")); return; }
  VOICE.ctx = new (window.AudioContext || window.webkitAudioContext)();
  VOICE.an = VOICE.ctx.createAnalyser(); VOICE.an.fftSize = 1024;
  VOICE.ctx.createMediaStreamSource(VOICE.stream).connect(VOICE.an);
  meter(); vListen();
}
function rms() { const buf = new Uint8Array(VOICE.an.fftSize); VOICE.an.getByteTimeDomainData(buf); let s = 0; for (const v of buf) s += (v - 128) ** 2; return Math.sqrt(s / buf.length); }
// VAD = level above an ADAPTIVE noise floor AND most energy in the voice band (300–3400 Hz).
// Engine rumble (low) and compressor hiss (high) raise the floor but do not count as speech.
function voiceBand() {
  // share of POWER (not dB) in 300–3400 Hz among 80–8000 Hz
  const an = VOICE.an, f = new Float32Array(an.frequencyBinCount); an.getFloatFrequencyData(f);
  const hz = VOICE.ctx.sampleRate / an.fftSize; let band = 0, all = 0;
  for (let i = 1; i < f.length; i++) {
    const fr = i * hz; if (fr < 80 || fr > 8000 || !isFinite(f[i])) continue;
    const p = Math.pow(10, f[i] / 10); all += p; if (fr >= 300 && fr <= 3400) band += p;
  }
  return all ? band / all : 0;
}
function isSpeech(r, k = 2.2, min = 7) {
  const floor = VOICE.floor || 4, thr = Math.max(min, floor * k + 2);
  const b = r > thr ? voiceBand() : 0; VOICE.band = b;
  if (r < thr) VOICE.floor = Math.min(12, 0.98 * floor + 0.02 * r);  // background level; capped so speech never becomes "noise"
  return r > thr && b > 0.45;
}
function meter() {
  if (!VOICE.on) return;
  const now = performance.now(), dt = VOICE.last ? now - VOICE.last : 16; VOICE.last = now;
  const r = rms(); $("#vOrb").style.setProperty("--lvl", Math.min(1, r / 30).toFixed(2));
  if (VOICE.state === "listening") {
    if (isSpeech(r)) { VOICE.speechMs += dt; if (VOICE.speechMs > 150) VOICE.heard = true; VOICE.quietMs = 0; } else if (VOICE.heard) VOICE.quietMs += dt;
    const long = now - VOICE.t0;
    if ((VOICE.heard && VOICE.quietMs > 1300) || long > 30000) vStopRec();
    else if (!VOICE.heard && long > 12000) { vSet("idle", t("v_tap_to_talk")); stopRecorder(); }
  } else if (VOICE.state === "speaking") {  // barge-in: the mechanic starts talking over the answer
    if (isSpeech(r, 3, 16)) { VOICE.speechMs += dt; if (VOICE.speechMs > 350) { Player.stop(); vListen(); } } else VOICE.speechMs = 0;
  }
  VOICE.raf = requestAnimationFrame(meter);
}
function vListen() {
  if (!VOICE.on || !VOICE.stream) return;
  Player.stop();
  VOICE.chunks = []; VOICE.heard = false; VOICE.quietMs = 0; VOICE.speechMs = 0; VOICE.t0 = performance.now();
  VOICE.rec = new MediaRecorder(VOICE.stream);
  VOICE.rec.ondataavailable = e => e.data.size && VOICE.chunks.push(e.data);
  VOICE.rec.onstop = vOnRecorded;
  VOICE.rec.start(250); vSet("listening");
}
function stopRecorder() { if (VOICE.rec && VOICE.rec.state !== "inactive") { VOICE.rec.onstop = null; VOICE.rec.stop(); } VOICE.rec = null; }
function vStopRec() { if (VOICE.rec && VOICE.rec.state !== "inactive") { vSet("transcribing"); VOICE.rec.stop(); } }
async function vOnRecorded() {
  const blob = new Blob(VOICE.chunks, {type: (VOICE.rec && VOICE.rec.mimeType) || "audio/webm"}); VOICE.rec = null;
  if (!VOICE.on) return;
  if (blob.size < 1500) return vListen();
  const fd = new FormData(); fd.append("audio", blob, "voice.webm"); fd.append("lang", ME.lang || "ru");
  let text = "";
  try { text = ((await api("/api/stt", {method: "POST", body: fd})).text || "").trim(); }
  catch (e) { if (!VOICE.on) return; vSet("idle", friendlyError(e)); return; }
  if (!VOICE.on) return;
  if (!text) { vSet("listening", t("v_didnt_hear")); return vListen(); }
  $("#vSaid").textContent = text; $("#vReply").textContent = "";
  vSet("thinking");
  const turn = await send(text);
  if (!VOICE.on) return;
  const reply = turn && (turn.text || (turn.cards || []).map(c => c.light_text || "").join(" "));
  if (!reply) { vSet("idle", t("v_no_answer")); return; }
  $("#vReply").textContent = plain(reply).slice(0, 400) + (plain(reply).length > 400 ? "…" : "");
  if (!A11Y.voiceSpeaks()) return vListen();
  vSet("speaking"); VOICE.speechMs = 0;
  Player.play(reply, null, {onend: () => { if (VOICE.on && VOICE.state === "speaking") vListen(); }});
}
function voiceClose() {
  VOICE.on = false; cancelAnimationFrame(VOICE.raf); stopRecorder(); Player.stop();
  try { VOICE.stream && VOICE.stream.getTracks().forEach(tr => tr.stop()); } catch (_) {}
  try { VOICE.ctx && VOICE.ctx.close(); } catch (_) {}
  VOICE.stream = VOICE.ctx = VOICE.an = null; VOICE.last = 0;
  $("#voice").classList.add("hidden");
}
function orbTap() {
  if (VOICE.state === "listening") vStopRec();
  else if (VOICE.state === "speaking") { Player.stop(); vListen(); }
  else if (VOICE.state === "idle") vListen();
}

// ---------------------------------------------------------------- sheets
function sheet(title, body, init) {
  closeSheet();
  $("#layer").innerHTML = `<div class="scrim" id="scrim"></div><div class="sheet" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="grab"></div><div class="sh"><h2>${esc(title)}</h2><button class="icon-btn" id="shClose" aria-label="${esc(t("close"))}">${icon("x")}</button></div><div class="sb">${body}</div></div>`;
  $("#scrim").onclick = closeSheet; $("#shClose").onclick = closeSheet;
  init && init($("#layer .sheet"));
}
function closeSheet() { $("#layer").innerHTML = ""; }
const short = s => String(s).split(/\s[—–-]\s|\s\(/)[0];
function segHtml(id, opts, cur) { return `<div class="seg" id="${id}">${opts.map(([v, l]) => `<button data-v="${v}" class="${v === cur ? "on" : ""}">${esc(l)}</button>`).join("")}</div>`; }
function bindSeg(root, id, fn) { root.querySelectorAll(`#${id} button`).forEach(b => b.onclick = () => { root.querySelectorAll(`#${id} button`).forEach(x => x.classList.toggle("on", x === b)); fn(b.dataset.v); }); }
function settingsSheet() {
  sheet(t("settings"),
    `<div class="field"><label class="label" for="s_lang">${t("language")}</label><select id="s_lang" class="input"></select></div>` +
    `<div class="field"><label class="label" for="s_alang">${t("answer_lang")}</label><select id="s_alang" class="input"></select></div>` +
    `<div class="field"><span class="label">${t("level")}</span>${segHtml("s_exp", [["novice", short(t("exp_novice"))], ["experienced", short(t("exp_expert"))], ["expert", short(t("exp_pro"))]], ME.experience || "experienced")}</div>` +
    `<div class="field"><span class="label">${t("detail")}</span>${segHtml("s_detail", [["brief", t("detail_brief")], ["detailed", t("detail_detailed")]], ME.detail || "brief")}</div>` +
    `<div class="field"><span class="label">${t("font_size")}</span>${segHtml("s_fs", [["n", "A"], ["l", "A+"], ["xl", "A++"]], A11Y.size())}</div>` +
    `<div class="field"><span class="label">${t("theme")}</span>${segHtml("s_theme", [["dark", t("theme_dark")], ["light", t("theme_light")], ["contrast", t("theme_contrast")]], A11Y.theme())}</div>` +
    `<label class="switch"><span>${t("voice_speaks")}</span><input type="checkbox" id="s_vtts" ${A11Y.voiceSpeaks() ? "checked" : ""}></label>` +
    `<label class="switch"><span>${t("captions")}</span><input type="checkbox" id="s_cc" ${A11Y.captions() ? "checked" : ""}></label>` +
    `<div class="sep"></div><p class="small muted">${t("privacy_note")} <a href="/privacy" target="_blank">${t("policy")}</a></p>` +
    `<div class="card-actions"><button class="btn ghost" id="s_logout">${icon("log-out", 18)}<span>${t("logout")}</span></button><button class="btn danger" id="s_delete">${t("delete_data")}</button></div>`, sh => {
    langOptions(sh.querySelector("#s_lang"), ME.lang); langOptions(sh.querySelector("#s_alang"), ME.answer_lang || "", true);
    sh.querySelector("#s_lang").onchange = async e => { await saveProfile({lang: e.target.value}); settingsSheet(); if (VID) loadChips(); else showEmpty(); };
    sh.querySelector("#s_alang").onchange = e => saveProfile({answer_lang: e.target.value});
    bindSeg(sh, "s_exp", v => saveProfile({experience: v}));
    bindSeg(sh, "s_detail", v => saveProfile({detail: v}));
    bindSeg(sh, "s_fs", v => { A11Y.setSize(v); layout(); });
    bindSeg(sh, "s_theme", v => A11Y.setTheme(v));
    sh.querySelector("#s_vtts").onchange = e => A11Y.setVoiceSpeaks(e.target.checked);
    sh.querySelector("#s_cc").onchange = e => A11Y.setCaptions(e.target.checked);
    sh.querySelector("#s_logout").onclick = async () => { await api("/api/logout", {method: "POST"}); location.reload(); };
    sh.querySelector("#s_delete").onclick = async () => { if (confirm(t("delete_confirm"))) { await api("/api/me", {method: "DELETE"}); location.reload(); } };
  });
}
async function saveProfile(patch) { try { ME = (await api("/api/me", {json: patch})).user; applyI18n(); } catch (e) { toast(friendlyError(e)); } }

// ---------------------------------------------------------------- wiring
document.addEventListener("click", e => {
  const ch = e.target.closest("[data-doc][data-page]");
  if (ch) { e.preventDefault(); openCitation(ch.dataset.doc, +ch.dataset.page, ch.dataset.q || ""); }
});
document.addEventListener("keydown", e => { if (e.key === "Escape") { if (!$("#viewer").classList.contains("hidden")) closeViewer(); else if ($("#layer").innerHTML) closeSheet(); else if (VOICE.on) voiceClose(); } });
$("#mClose").onclick = closeViewer;
$("#btnSettings").onclick = settingsSheet;
$("#btnMenu").onclick = vehicleMenu;
$("#vehBtn").onclick = pickVehicleSheet;
$("#send").onclick = () => send();
$("#txt").addEventListener("input", autosize);
$("#txt").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
$("#photo").onchange = onPhoto;
$("#mic").onclick = voiceOpen;
$("#vClose").onclick = voiceClose; $("#vExit").onclick = voiceClose; $("#vOrb").onclick = orbTap;
window.addEventListener("resize", layout);

let ROLE = "mechanic";
$$("#l_role button").forEach(b => b.onclick = () => { ROLE = b.dataset.v; $$("#l_role button").forEach(x => x.classList.toggle("on", x === b)); });
$("#l_go").onclick = async () => {
  const body = {name: $("#l_name").value.trim() || "—", role: ROLE, lang: $("#l_lang").value, experience: "experienced", consent: $("#l_consent").checked};
  if (!body.consent) return toast(t("consent_needed"));
  try { ME = (await api("/api/login", {json: body})).user; } catch (e) { return toast(friendlyError(e)); }
  if (ME.role === "manager") location.href = "/manager"; else start();
};
async function start() {
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  applyI18n(); await loadVehicles(); autosize();
}
(async () => {
  hydrateIcons();
  I18N = await (await fetch("/static/i18n.json")).json();
  try { ME = (await api("/api/session")).user; if (!ME) throw new Error("login"); start(); }
  catch (e) { langOptions($("#l_lang"), (navigator.language || "ru").slice(0, 2)); $("#l_lang").onchange = applyI18n; applyI18n(); $("#login").classList.remove("hidden"); }
})();
