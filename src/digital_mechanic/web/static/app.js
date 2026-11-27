"use strict";
// Mechanic app. Vanilla JS, no build step. Sections: core · i18n · audio player · rendering · vehicle · chat · citation viewer · voice mode · sheets · settings · login.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
let LAST_CHECK = null, LAST_DRAFT = null;  // {vid, sid} of the check diagnosed in this dialog: sent with chat (Vercel instances do not share it)
let I18N = {}, ME = null, VID = new URLSearchParams(location.search).get("v") || localStorage.getItem("vid") || "", VEH = null, VLIST = [], BUSY = false, LAST_Q = "", HYP = false;
const SPEECH_LANG = {ru: "ru-RU", uz: "uz-UZ", tg: "tg-TJ", kk: "kk-KZ", hi: "hi-IN", vi: "vi-VN", bn: "bn-IN", ky: "ky-KG"};
const LANG_NAMES = {ru: "Русский", uz: "Oʻzbekcha", tg: "Тоҷикӣ", kk: "Қазақша", hi: "हिन्दी", vi: "Tiếng Việt", bn: "বাংলা", ky: "Кыргызча"};  // = i18n.LANGS
const t = k => (I18N[(ME && ME.lang) || $("#l_lang")?.value || "ru"] || I18N.ru || {})[k] || (I18N.ru || {})[k] || k;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const TT = (k, dflt) => { const L = (ME && ME.lang) || $("#l_lang")?.value || "ru"; return (I18N[L] || {})[k] || dflt; };  // per-language string or the given (Russian) text
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
  if ($("#l_country")) loginCountry();
}
// Personal-data law by country: consent text names the law of the selected country, in the UI language.
let COUNTRIES = null;
async function loadCountries() { if (!COUNTRIES) COUNTRIES = (await (await fetch("/api/countries?lang=" + ((ME && ME.lang) || $("#l_lang")?.value || "ru"))).json()).countries; return COUNTRIES; }
function countryOptions(sel, cur) { sel.innerHTML = (COUNTRIES || []).map(c => `<option value="${c.code}" ${c.code === cur ? "selected" : ""}>${esc(c.name)}</option>`).join(""); }
function consentText(code) { const c = (COUNTRIES || []).find(x => x.code === code) || {short: "152-ФЗ"}; return t("consent_law").replace("{law}", c.short); }
async function loginCountry() {
  await loadCountries(); const sel = $("#l_country"); const cur = sel.value || "RU";
  if (!sel.options.length) countryOptions(sel, cur);
  $("#l_consent_t").textContent = consentText(sel.value); $("#l_policy").href = `/privacy?country=${sel.value}`;
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
  async play(text, btn = null, {onend = null, onlevel = null, original = null, lang = null} = {}) {
    this.stop();
    const raw = plain(text); if (!raw) return onend && onend();
    const seq = this.seq;
    if (btn) { this.btn = btn; btn.innerHTML = icon("square", 16); btn.classList.add("on"); btn.setAttribute("aria-label", t("stop")); }
    const done = () => { if (seq !== this.seq) return; if (this.btn) { this.btn.innerHTML = icon("play", 18); this.btn.classList.remove("on"); this.btn = null; } this.audio = null; caption(""); onend && onend(); };
    const voices = await ttsVoices();
    if (seq !== this.seq) return;
    const plan = ttsPlan(raw, original != null ? plain(original) : raw, lang || speakLang(), voices);
    if (plan.noticeKey) toast((t(plan.noticeKey) || plan.noticeKey).replace("{lang}", plan.noticeLang || ""));
    const p = plan.text || raw;
    const spokenLang = langCode(plan.lang || lang || speakLang());
    const serverHasVoice = SERVER_TTS && (SERVER_TTS_LANGS.includes(spokenLang) || (plan.useServer && (!SERVER_TTS_LANGS.length || SERVER_TTS_LANGS.includes("ru"))));
    if (serverHasVoice) {
      try {
        const r = await fetch("/api/tts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text: p, lang: spokenLang})});
        if (seq !== this.seq) return;
        if (r.ok) {
          const a = new Audio(URL.createObjectURL(await r.blob())); this.audio = a;
          a.ontimeupdate = () => { if (a.duration) captionFollow(p, a.currentTime / a.duration); };
          a.onended = done; a.onerror = done; await a.play().catch(done); return;
        }
        if (r.status === 404) SERVER_TTS = false;  // no server voice on this stand; 400/502 = only this language/text failed
      } catch (_) { SERVER_TTS = false; }
    }
    if (!plan.speak || !window.speechSynthesis) return done();
    const u = new SpeechSynthesisUtterance(p); u.lang = plan.lang || "ru-RU";
    if (plan.voice) { u.voice = plan.voice; u.lang = plan.voice.lang || u.lang; }
    u.onboundary = e => captionFollow(p, (e.charIndex || 0) / p.length); u.onend = done; u.onerror = done;
    speechSynthesis.speak(u);
  },
};
let SERVER_TTS = true, SERVER_STT = true, SERVER_TTS_LANGS = [];
function speakLang() { return (typeof needsTr === "function" && needsTr() && ME && ME.lang) ? ME.lang : (ALANG() || "ru"); }
function speakVisible(root, fallback) {
  if (!root) return fallback;
  const skip = "[data-notr],button,script,style,code,.mono,.cite,.src,.msg-actions,.card-actions,.sources";
  const parts = [];
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: n => {
    const pe = n.parentElement; if (!pe || pe.closest(skip)) return 3;
    return n.nodeValue && n.nodeValue.trim() ? 1 : 3;
  }});
  for (let n; (n = w.nextNode());) parts.push(n.nodeValue);
  const s = parts.join(" ").replace(/\s+/g, " ").trim();
  return s || fallback;
}
function ttsVoices() {
  return new Promise(resolve => {
    if (!window.speechSynthesis) return resolve([]);
    const grab = () => speechSynthesis.getVoices() || [];
    const cur = grab(); if (cur.length) return resolve(cur);
    let done = false;
    const finish = () => { if (done) return; done = true; try { speechSynthesis.removeEventListener("voiceschanged", finish); } catch (_) {} resolve(grab()); };
    try { speechSynthesis.addEventListener("voiceschanged", finish); } catch (_) { return resolve(cur); }
    setTimeout(finish, 800);
  });
}
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
  // a label without a source (e.g. in the handoff summary) disappears together with the space before it («ТНВД ;» -> «ТНВД;»)
  h = h.replace(/\[([^\]]{1,80})\]/g, (m, grp, off, whole) => {
    const labs = grp.match(/\b(?:S|X)\d+\b/g); if (!labs) return m;
    if (!labs.some(l => src[l])) return "\u0000";
    const ctx = whole.slice(Math.max(0, off - 240), off).replace(/<[^>]+>/g, "").split(/\n|[.!?]\s|•/).filter(x => x.trim().length > 6).pop() || "";
    return labs.map(l => src[l] ? `<button class="cite ${src[l].extra ? "x" : ""}" data-doc="${esc(src[l].doc_id)}" data-page="${src[l].page}" data-q="${esc(ctx.trim().slice(-200))}" title="${esc(src[l].document)}">${t("page_short")} ${src[l].page}</button>` : "").join("");
  }).replace(/[ \t]*\u0000/g, "");
  return collapseCites(h);
}
// «Подробнее» from one manual page: «с. 52» after each of 10 paragraphs (Vercel 27.11 08:30, 1200 px). When every chip of the
// answer is the same page, keep the first (seen at once) and the last (where reading ends); answers with several pages untouched.
function onePageCites(h) {
  const re = /[ \t]*<button class="cite[^"]*" data-doc="([^"]*)" data-page="(\d+)"[^>]*>[^<]*<\/button>/g;
  const ms = [...h.matchAll(re)]; if (ms.length < 3 || new Set(ms.map(m => m[1] + "#" + m[2])).size > 1) return h;
  let out = "", pos = 0;
  ms.forEach((m, i) => { if (i > 0 && i < ms.length - 1) { out += h.slice(pos, m.index); pos = m.index + m[0].length; } });
  return out + h.slice(pos);
}
function collapseCites(h, block = false) {
  // «работа [с. 52], работа [с. 52], работа [с. 52]» -> one chip after the last item: the same page cited clause after
  // clause in one paragraph cluttered the answer; a chip for another page in between breaks the run.
  // block: a whole paragraph or list (lines, items, sentences) — «с. 52» after each of 4–5 lines on a 390 px screen (audit 23:17)
  const re = /[ \t]*<button class="cite[^"]*" data-doc="([^"]*)" data-page="(\d+)"[^>]*>[^<]*<\/button>/g;
  const ms = [...h.matchAll(re)]; if (ms.length < 2) return h;
  const drop = new Set();
  for (let i = 0; i + 1 < ms.length; i++) {
    const a = ms[i], b = ms[i + 1], gap = h.slice(a.index + a[0].length, b.index);
    if (a[1] === b[1] && a[2] === b[2] && (block || gap.length < 240 && !/<(b|br)>/.test(gap) && !/[.!?]\s/.test(gap))) drop.add(i);
  }
  if (!drop.size) return h;
  let out = "", pos = 0;
  ms.forEach((m, i) => { if (drop.has(i)) { out += h.slice(pos, m.index); pos = m.index + m[0].length; } });
  return out + h.slice(pos);
}
// The free model sometimes returns the whole detailed answer as one line: «… [S1]. **Что проверить** 1. … [S1]. 2. … [S1]. 3. …»
// (Vercel 26.11 22:30: «Подробнее» rendered as one wall of text). Put bold headings and a 1-2-3 run back on their own lines.
function unflatten(text) {
  return String(text || "").split("\n").map(line => {
    line = line.replace(/([.!?:;\]])\s+(\*\*[^*\n]{3,48}\*\*)/g, "$1\n$2");
    return line.split("\n").map(l => {
      if (!/(^|[\s*])1[.)]\s+\S/.test(l) || !/[.;:!?\]]\s+2[.)]\s+\S/.test(l)) return l;
      let n = 2;
      l = l.replace(/([.;:!?\]])\s+(\d{1,2})[.)]\s+(?=\S)/g, (m, p, d) => (+d === n ? (n++, `${p}\n${d}. `) : m));
      return n > 3 ? l.replace(/(\S)\s+1[.)]\s+(?=\S)/, "$1\n1. ") : l.replace(/\n(\d{1,2})\. /g, " $1. ");
    }).join("\n");
  }).map(chunkWall).join("\n");
}
// A brief answer the model wrote as one 400–600 char paragraph (Vercel 00:30, 390 px: «что значит F08?» — 14 lines of
// one block): sentences grouped into short paragraphs of ≤ ~220 chars. Lists, headings and short lines are left alone.
function chunkWall(line) {
  if (line.length < 300 || line.includes("\n") || /^\s*(\d{1,2}[.)]|[-*•#>])\s/.test(line)) return line;
  const ss = line.split(/(?<=[.!?…][»"]?|\][.!?])\s+(?=[«"]?[А-ЯЁA-Z])/);
  if (ss.length < 3) return line;
  const out = []; let cur = "";
  for (const x of ss) { if (cur && cur.length + x.length > 220) { out.push(cur); cur = x; } else cur = cur ? cur + " " + x : x; }
  if (cur) out.push(cur);
  return out.join("\n\n");
}
function md(text, sources) {
  const src = Object.fromEntries((sources || []).map(s => [s.label, s]));
  const lines = esc(unflatten(text)).replace(/((?:[ \t]*\[[SX]\d+\])+)([.;:!?])(?=\s|$)/g, "$2$1")  // «пуска [S1].» -> «пуска. [S1]»: no lone «.» wrapped under the chip (390 px)
    .replace(/\r/g, "").replace(/[ \t]+([.,;:!?)])/g, "$1").replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]\uFE0F?\s?/gu, "").split("\n");
  let out = "", list = null, para = [];
  const flushP = () => { if (para.length) { out += `<p>${collapseCites(inline(para.join("<br>"), src), true)}</p>`; para = []; } };
  const flushL = () => { if (list) { out += `<${list.tag}>${collapseCites(list.items.map(i => `<li>${inline(i, src)}</li>`).join(""), true)}</${list.tag}>`; list = null; } };
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
  return onePageCites(out);
}
const ACT_ICON = {marks_mismatch: "x", marks_match: "check", create_ticket: "file-text", confirm_ticket: "check", next_step: "check", start_repair: "wrench", show_section: "book-open", handoff: "forward"};
function answerActionsHtml(actions) {
  if (!actions || !actions.length) return "";
  return `<div class="answer-actions">` + actions.map((a, i) => `<button class="btn sm ${i === 0 && a.kind === "send" ? "primary" : "ghost"}" data-ai="${i}">${icon(ACT_ICON[a.id] || "chevron-right", 16)}<span>${esc(t("act_" + a.id) !== "act_" + a.id ? t("act_" + a.id) : a.label)}</span></button>`).join("") + `</div>`;
}
function bindAnswerActions(d, actions) {
  (actions || []).forEach((a, i) => { const b = d.querySelector(`[data-ai="${i}"]`); if (!b) return;
    b.onclick = ev => { ev.stopPropagation();
      if (a.kind === "send") { d.querySelector(".answer-actions")?.remove(); send(a.text); }
      else if (a.kind === "open") openCitation(a.doc_id, a.page, "");
      else if (a.kind === "handoff") handoffSheet(); }; });
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
  if (speak) acts.unshift({icon: "play", aria: t("play"), cls: "play", onclick: (ev, b) => (Player.btn === b ? Player.stop() : Player.play(speakVisible(d.querySelector(".b"), speak), b, {original: speak, lang: speakLang()}))});
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
function skeleton(text) {
  const d = addMsg("bot", `<div class="progress" data-notr>${icon("loader-circle", 16, "spin")}<span class="ptext">${esc(text || t("thinking"))}</span></div><div class="line"></div><div class="line"></div><div class="line"></div><div class="line"></div>`, {cls: "skeleton"});
  d.dataset.busy = 1;
  return d;
}
function errorMsg(e) { return addMsg("bot", `<span class="err">${esc(friendlyError(e))}</span>`); }
// A proxy/tunnel error page (Cloudflare 530/502 HTML, 27.11 10:17 the deb tunnel lost its edge) or a dropped connection
// («Failed to fetch») must not be printed into the chat as raw markup: say the request did not go through.
function friendlyError(e) {
  const m = String(e && e.message || e);
  if (/<(!doctype|html|head|body|title)\b/i.test(m) || /^(TypeError: )?(Failed to fetch|NetworkError|Load failed|network error)/i.test(m)) return t("err_generic");
  if (e && e.status === 502) return m.includes("ML") ? t("err_ml") : m.includes("ТОиР") ? t("err_toir") : m;
  if (e && e.status === 401) return t("err_session");
  return m || t("err_generic");
}

function renderTurn(turn, expanded = false) {
  const citesBefore = new Set(document.querySelectorAll("#chat .cite"));
  (turn.cards || []).forEach(renderCard);
  if (turn.preface) { /* already part of text */ }
  if (turn.text) {
    const hasSrc = (turn.sources || []).length > 0;
    // «стучит → стук», «клапана → клапаны» are just word forms: show only slang, abbreviations and typos
    const pre = (a, b) => { let i = 0; while (i < a.length && a[i] === b[i]) i++; return i; };
    const uu = (turn.understood || []).filter(([a, b]) => !(b.length <= a.length && pre(a.toLowerCase(), b.toLowerCase()) >= 3));
    const und = uu.length ? `<div class="understood">${t("understood")}: ${uu.map(([a, b]) => `${esc(a)} → <b>${esc(b)}</b>`).join(", ")}</div>` : "";
    const actions = [];
    const isDraft = (turn.cards || []).some(c => c.kind === "ticket_draft" || c.kind === "ticket_created");  // the card is the detail: no «Подробнее» under it
    if (hasSrc && !expanded && !isDraft && (ME.detail || "brief") === "brief") actions.push({label: t("expand"), onclick: (ev, b) => { b.remove(); expandClick(turn); }});
    if (hasSrc && VID) actions.push({icon: "ellipsis", aria: t("more_actions"), onclick: () => turnMenu(turn)});
    const d = addMsg("bot", und + factsHtml(turn.facts) + md(turn.text, turn.sources) + answerActionsHtml(turn.actions), {speak: turn.text, actions});
    bindAnswerActions(d, turn.actions);
    if (actions.some(a => a.label === t("expand"))) prefetchExpand(turn);
  }
  loadChips();
  prefetchExtracts([...document.querySelectorAll("#chat .cite")].filter(b => !citesBefore.has(b)));
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
  if (c.kind === "ticket_draft") LAST_DRAFT = c.token ? {vid: VID, token: c.token} : null; else if (c.kind === "ticket_created") LAST_DRAFT = null;
  if (c.kind === "verdict") {
    const ch = c.check;
    const head = c.status === "anomaly" ? `${ch.anomaly_type} · ${esc(ch.anomaly_title)}` : c.status === "normal" ? t("verdict_normal") : t("verdict_failed");
    let h = `<div class="vh"><div class="vi">${icon(LIGHT_ICON[c.light] || "activity", 22)}</div><div><div class="vt">${head}</div>` +
      (c.light_text && c.status === "anomaly" ? `<div class="vs">${esc(c.light_text)}</div>` : "") + `</div></div>`;
    h += `<div class="kv"><span>${t("session")}</span><span class="mono">${esc(ch.session_id)}, ${esc(typeof fmtDate === "function" ? fmtDate(ch.checked_at, true) : (ch.checked_at || "").slice(0, 16).replace("T", " "))}</span>`;
    if (ch.anomaly_score != null) h += `<span>${t("score")}</span><span class="mono">${ch.anomaly_score.toFixed(2).replace(".", ALANG() === "hi" ? "." : ",")}</span>`;
    if (ch.confidence != null) h += `<span>${t("confidence")}</span><span class="mono">${Math.round(ch.confidence * 100)}%</span>`;
    h += `<span>${t("quality")}</span><span>${esc(t(QUALITY[ch.signal_quality] || ch.signal_quality))}</span>`;
    if (ch.description) h += `<span>${t("ml_says")}</span><span>${esc(ch.description)}</span>`;
    h += `</div>`;
    if (c.doubt && c.doubt.length) h += `<p class="warn-text small" style="margin-top:12px">${t("doubt")}: ${c.doubt.map(esc).join("; ")}</p>`;
    if (c.status === "anomaly" && c.thresholds) h += `<p class="small muted conf-threshold" style="margin-top:8px">${esc(t("conf_threshold"))}</p>`;
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
    if (c.warning) h += `<p class="warn-text small">${t("doubt")}: ${esc(c.warning)}</p>`;
    if (c.prohibition) h += `<p class="err small"><b>${t("prohibition")}:</b> «${esc(c.prohibition.quote)}» <button class="cite" data-doc="${esc(c.prohibition.doc_id)}" data-page="${c.prohibition.page}" data-q="${esc(c.prohibition.quote)}">${t("page_short")} ${c.prohibition.page}</button></p>`;
    if (c.urgency_options) h += `<label class="small urg-label">${t("priority_pick")}<select class="urg-pick" aria-label="${esc(t("priority_pick"))}">` +
      ["high", "medium", "low"].map(u => `<option value="${u}"${u === r.urgency ? " selected" : ""}>${esc(c.urgency_options[u] || u)}</option>`).join("") + `</select></label>`;
    h += `<details class="more"><summary>${icon("chevron-right", 16)}${t("json_toir")}</summary><pre class="json">${esc(JSON.stringify(r, null, 1))}</pre></details><div class="card-actions"></div>`;
    const d = addMsg("bot", h);
    const row = d.querySelector(".card-actions");
    const ok = document.createElement("button"); ok.className = "btn primary"; ok.textContent = c.requires_inspection ? t("warn_ack") : t("confirm_send");
    ok.onclick = () => confirmTicket(c.draft_id, true, row, c.token, d.querySelector("select.urg-pick")?.value);
    const no = document.createElement("button"); no.className = "btn ghost"; no.textContent = c.requires_inspection ? t("inspection_no") : t("cancel");
    no.onclick = () => c.requires_inspection ? confirmTicket(c.draft_id, false, row, c.token) : (row.innerHTML = `<span class="muted small">${t("cancelled")}</span>`);
    row.append(ok, no);
  } else if (c.kind === "contradiction") {
    let h = `<div class="card-title">${icon("triangle-alert", 20)}${t("contradiction")}: ${esc(c.topic)}</div><ul class="small">` +
      c.sides.map(sd => `<li>${esc(sd.document)}: «${esc(sd.quote)}» <button class="cite" data-doc="${esc(sd.doc_id)}" data-page="${sd.page}" data-q="${esc(sd.quote)}">${t("page_short")} ${sd.page}</button></li>`).join("") + `</ul>` +
      `<p><b>${t("safe_rule")}:</b> ${esc(c.safe_rule)}</p>` + (c.resolution ? `<details class="more"><summary>${icon("chevron-right", 16)}${t("why_rule")}</summary><div class="small">${esc(c.resolution)}</div></details>` : "") +
      `<p class="small muted">${esc(c.clarify)}</p>`;
    addMsg("bot", h, {cls: "verdict service", speak: `${t("contradiction")}. ${t("safe_rule")}: ${c.safe_rule}`});
  } else if (c.kind === "no_instruction") {
    const d = addMsg("bot", `<div class="card-title">${icon("triangle-alert", 20)}${t("no_ticket")}</div><p>${esc(c.reason)}</p>` +
      `<div class="label">${t("defect_rec")}</div><p class="small rec-text">${esc(c.recommendation)}</p><div class="card-actions"></div>`, {speak: c.reason});
    const b = document.createElement("button"); b.className = "btn ghost"; b.textContent = t("copy");
    b.onclick = () => { try { navigator.clipboard.writeText(c.recommendation); b.textContent = t("copied"); } catch (_) {} };
    d.querySelector(".card-actions").append(b);
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
// One request per (vehicle, code) a minute: the history replay drew 13 check cards and fired 13 identical /api/proven calls
// (local probe 27.11 12:50, M333YK47) on top of the vehicle, history and suggest requests.
const PROVEN = new Map();
function provenFor(vid, code) {
  const k = vid + "|" + code, hit = PROVEN.get(k);
  if (hit && Date.now() - hit.t < 60000) return hit.p;
  const p = api(`/api/proven?vehicle_id=${encodeURIComponent(vid)}&code=${encodeURIComponent(code)}`);
  PROVEN.set(k, {t: Date.now(), p}); p.catch(() => PROVEN.delete(k));
  return p;
}
async function loadProven(box, code) {
  try {
    const r = await provenFor(VID, code);
    if (!r.fixes.length) return;
    const ver = f => f.verified === true ? `<span class="ok-text">${t("fix_verified")}</span>` : f.verified === false ? `<span class="warn-text">${t("fix_not_verified")}</span>` : "";
    box.insertAdjacentHTML("beforeend", `<details class="more proven"><summary>${icon("chevron-right", 16)}${t("proven_fixes")}: ${r.fixes.length}</summary>` +
      r.fixes.map(f => `<div class="fix"><div><b class="mono">${esc(f.vehicle_id)}</b> <span class="muted">${esc(f.date || "")}${f.ticket_id ? ", " + esc(f.ticket_id) : ""}</span></div><div class="small">${ver(f)}${f.source === "demo" ? ` <span class="muted">(${t("demo_hist")})</span>` : ""}</div><ol>` +
        f.steps.map(st => `<li>${esc(st.text)}${st.page ? ` <button class="cite" data-doc="${esc(st.doc_id)}" data-page="${st.page}" data-q="${esc(st.text)}">${t("page_short")} ${st.page}</button>` : ""}</li>`).join("") + `</ol></div>`).join("") + `</details>`);
  } catch (_) {}
}
async function confirmTicket(draftId, ok, row, token, urgency) {
  row.innerHTML = `<span class="muted small">${icon("loader-circle", 16, "spin")}</span>`;
  try {
    const r = await api("/api/ticket/confirm", {json: {vehicle_id: VID, draft_id: draftId, inspection_ok: ok, ...(urgency ? {urgency} : {}), ...(token ? {token} : {})}});
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
// questions already asked about this vehicle in this tab: their chip is not offered again (a tapped chip stayed in the row)
const askedKey = () => "asked:" + (VID || "");
const chipNorm = s => (s || "").toLowerCase().replace(/[^a-zа-яё0-9]+/gi, " ").trim();
function askedGet() { try { return JSON.parse(sessionStorage.getItem(askedKey()) || "[]"); } catch (_) { return []; } }
function askedAdd(text) { if (!VID) return; try { const a = askedGet(); a.push(chipNorm(text)); sessionStorage.setItem(askedKey(), JSON.stringify(a.slice(-40))); } catch (_) {} }
async function loadChips() {
  const q = $("#quick"); if (!q) return;
  if (!ME || !VID) { q.innerHTML = ""; return; }
  const seq = ++CHIP_SEQ;
  try {
    const r = await api(`/api/suggest?vehicle_id=${encodeURIComponent(VID)}`);
    if (seq !== CHIP_SEQ) return;
    q.innerHTML = "";
    const asked = new Set(askedGet());
    const fresh = (r.chips || []).filter(ch => ch.kind === "code" || !asked.has(chipNorm(ch.text)));
    fresh.slice(0, 6).forEach(ch => {
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
    if (m.text) m.role === "user" ? addMsg("user", esc(m.text)) : addMsg("bot", md(m.text, m.meta.sources), {speak: m.text});
  }
  if (r.draft && !r.messages.some(m => (m.meta.cards || []).some(c => c.kind === "ticket_draft"))) renderCard(r.draft);
  if (!r.messages.length && r.repair) renderCard({...r.repair, kind: "repair"});
  // day-2 briefing: the session belongs to the truck — another mechanic sees where the repair stopped and past repairs
  const rk = r.resume ? `resume:${vid}:${r.resume.repair_id || "h"}:${r.resume.step ?? ""}` : "";
  if (r.resume && (r.resume.kind === "repair" || !r.messages.length) && !sessionStorage.getItem(rk)) {
    addMsg("bot", md(r.resume.text), {cls: "resume", speak: r.resume.text.replace(/\*\*/g, "")});
    try { sessionStorage.setItem(rk, "1"); } catch (_) {}
  }
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
    r.sessions.slice().reverse().map((s, i) => `<option value="${esc(s.session_id)}">${esc(s.session_id)}, ${esc(typeof fmtDate === "function" ? fmtDate(s.checked_at) : (s.checked_at || "").slice(0, 10))}${i === 0 ? ", " + t("latest") : ""}</option>`).join("") +
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
    LAST_CHECK = r.card && r.card.check ? {vid: VID, sid: r.card.check.session_id} : null;
    renderCard(r.card); loadChips(); refreshVehicle();
    BUSY = false;
    await ask("", "explain_diagnosis");
  } catch (e) { errorMsg(e); }
  finally { BUSY = false; const b = $("#btnDiag"); if (b) b.disabled = false; }
}
// «Подробнее» streams (NDJSON from /api/chat/stream): on Vercel the /api/progress poll hits other instances and sees nothing,
// the stream carries the steps and live lines of the instance that writes the answer. Any stream problem -> plain /api/chat.
const EXPAND_STAGES = [[0, "📖 Перечитываю страницы руководства"], [3, "🧩 Собираю шаги и нормы с тех же страниц"], [7, "✍️ Пишу подробный ответ"], [14, "🔢 Сверяю цифры с руководством"]];
// Watchdog: the server ends a turn by 60 s, but a request can hang on the way (Vercel 27.11 02:35: a voice question waited 385 s,
// the server never saw it). No answer headers in 25 s, nothing new for 45 s or 80 s in all -> drop it, ask the plain way once.
const STREAM_WAIT = {head: 25000, idle: 45000, total: 80000};
async function chatStream(payload, onLine) {
  const ac = new AbortController(), t0 = Date.now(); let last = Date.now(), head = false;
  const dog = setInterval(() => { const n = Date.now(); if ((!head && n - t0 > STREAM_WAIT.head) || n - last > STREAM_WAIT.idle || n - t0 > STREAM_WAIT.total) ac.abort(); }, 1000);
  try { return await chatStreamRead(payload, onLine, ac, () => { head = true; last = Date.now(); }); }
  catch (e) { if (ac.signal.aborted) { const x = new Error("stream timeout"); x.stream = true; x.timeout = true; throw x; } throw e; }
  finally { clearInterval(dog); }
}
async function chatStreamRead(payload, onLine, ac, alive) {
  const r = await fetch("/api/chat/stream", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload), signal: ac.signal});
  alive();
  if (!r.ok || !r.body) { const e = new Error("stream " + r.status); e.status = r.status; e.stream = true; throw e; }
  const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "", fin = null;
  for (;;) {
    const {value, done} = await rd.read(); if (done) break; alive();
    buf += dec.decode(value, {stream: true}); let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (!line) continue;
      let m; try { m = JSON.parse(line); } catch (_) { continue; }
      if (m.turn) fin = m.turn; else if (m.error) { const e = new Error(m.error); e.status = m.status; throw e; } else onLine(m);
    }
  }
  if (!fin) { const e = new Error("stream ended"); e.stream = true; throw e; }
  return fin;
}
// Ticket draft (10–20 s on the free model): the same stream, timed stages say what is going on instead of one «думаю».
const TICKET_STAGES = [[0, "📖 Подбираю работы по руководству для этой неисправности"], [4, "📝 Составляю черновик заявки с цитатами"], [9, "🔎 Сверяю каждую цитату со страницей"], [15, "⚖️ Считаю приоритет по результату проверки"]];
const TICKET_ASK = /заявк|дефектовк/i;
// A typed question / explanation streams too (16–28 s on the free model at night, Vercel): the first verified lines show
// while the rest is written, the stage says what is going on; past ~30 s it says so honestly (the server stops at 60 s
// and answers from the manual pages).
const ASK_STAGES = [[0, "🔎 Ищу ответ в руководстве этого двигателя"], [4, "🧩 Выбираю нужные страницы"], [8, "✍️ Пишу ответ со ссылками на страницы"], [16, "🔢 Сверяю цифры с руководством"], [30, "⏳ Модель сейчас медленная — ещё немного"]];
async function ask(text, event = null, sources = null) {
  if (BUSY) return null; BUSY = true;
  const ticketIt = !event && TICKET_ASK.test(text || "") && !/подтвер/i.test(text) && !(LAST_DRAFT && LAST_DRAFT.vid === VID);
  const stages = ticketIt ? TICKET_STAGES : event === "expand" ? EXPAND_STAGES : ASK_STAGES;
  const streamIt = (event === "expand" || ticketIt || (!event && text) || event === "explain_diagnosis") && window.ReadableStream && window.TextDecoder;
  const th = skeleton(streamIt ? trStep(stages[0][1]) : ""), t0 = Date.now();
  let live = false;  // the stream delivered steps/lines: no /api/progress poll
  const showDraft = draft => {
    if (!draft || draft === th.dataset.draft || !th.isConnected) return;  // live lines already checked against the manual (numbers), replaced by the final answer
    th.dataset.draft = draft; th.classList.add("has-draft");
    let box = th.querySelector(".draft"); if (!box) { box = document.createElement("div"); box.className = "draft"; th.querySelector(".progress").after(box); }
    box.innerHTML = md(draft, []);
    vDraft(draft);
  };
  const tick = setInterval(async () => {
    const sec = Math.round((Date.now() - t0) / 1000);
    let steps = [], draft = "";
    if (!live) { try { const p = await api(`/api/progress?vehicle_id=${encodeURIComponent(VID || "")}`); steps = p.steps || []; draft = p.draft || ""; } catch (_) {} }
    if (!th.isConnected) return;
    const el = th.querySelector(".ptext");
    if (el && streamIt && !steps.length && (!th.dataset.sstep || (stages === ASK_STAGES && sec >= 30))) { const st = stages.filter(x => sec >= x[0]).pop(); el.dataset.step = trStep(st[1]); }  // stage text when nothing arrives
    if (el) { if (steps.length) el.dataset.step = trStep(steps[steps.length - 1]); el.textContent = `${th.dataset.draft ? t("writing") : (el.dataset.step || t("thinking"))} · ${sec} ${t("sec")}`; }
    showDraft(draft);
    if (VOICE.on && VOICE.state === "thinking" && el) $("#vHint").textContent = el.textContent;  // hands-free: the same stage and seconds as in the chat (was one static «Ищу ответ» for 15–30 s on Vercel)
  }, 1000);
  const payload = {vehicle_id: VID || null, text, event, ...(LAST_CHECK && LAST_CHECK.vid === VID ? {session_id: LAST_CHECK.sid} : {}), ...(LAST_DRAFT && LAST_DRAFT.vid === VID ? {draft_token: LAST_DRAFT.token} : {}), ...(sources ? {sources: sources.map(s => ({label: s.label, doc_id: s.doc_id, page: s.page}))} : {})};
  try {
    let r = null;
    if (streamIt) {
      try {
        r = await chatStream(payload, m => {
          live = true; const el = th.querySelector(".ptext");
          if (m.step && el && !ticketIt && !m.step.startsWith("📖")) { th.dataset.sstep = 1; el.dataset.step = trStep(m.step); }  // ticket: the tool steps come at the very end, the timed stages tell more  // «re-reading the pages» steps: keep the timed stages going
          if (m.draft) showDraft(m.draft);
        });
      } catch (e) { if (!e.stream) throw e; r = null; }  // the stream itself failed (proxy, old server): ask the plain way
    }
    if (!r) r = await api("/api/chat", {json: payload, signal: AbortSignal.timeout ? AbortSignal.timeout(90000) : undefined});
    if (r && r.draft_open === false) LAST_DRAFT = null;
    th.remove(); return renderTurn(r, event === "expand");
  }
  catch (e) { th.remove(); errorMsg(e); return null; }
  finally { clearInterval(tick); BUSY = false; }
}
// «Подробнее» is written while the mechanic reads the brief answer (free model: 15–44 s at night, Vercel 27.11): the request starts
// when the brief answer is shown, is not stored, and comes back signed; the click shows it at once (or waits for that run, with the
// stages counting from its start) and only then stores it. Not in the hands-free dialog. Any failure -> the usual streamed run.
const EXPANDS = new Map();
function prefetchExpand(turn) {
  if (VOICE.on || !VID || EXPANDS.has(turn)) return;
  const payload = {vehicle_id: VID, text: turn.text, event: "expand", prefetch: true, ...(LAST_CHECK && LAST_CHECK.vid === VID ? {session_id: LAST_CHECK.sid} : {}),
    sources: (turn.sources || []).map(s => ({label: s.label, doc_id: s.doc_id, page: s.page}))};
  const x = {t0: Date.now(), vid: VID, r: undefined, started: false};
  x.p = new Promise(res => { x.job = () => { x.started = true; x.t0 = Date.now();
    return api("/api/chat", {json: payload, signal: AbortSignal.timeout ? AbortSignal.timeout(90000) : undefined})
      .then(r => (r && r.sig && r.text ? r : null)).catch(() => null).then(r => { x.r = r; res(r); }); }; });
  if (EXPANDS.size > 20) EXPANDS.delete(EXPANDS.keys().next().value);
  EXPANDS.set(turn, x);
  bgPush(x.job, true);
}
// Background requests go in two lanes — «Подробнее» (above) and the citation extracts — one at a time in each, and none starts
// while a question is being answered: the free model slows down with parallel calls (Vercel 27.11 03:30: a chip question took
// 102 s next to three background calls), while one lane for all left the extracts unfetched behind a 20–30 s «Подробнее» (6–8 s per tap).
const BG = {x: {q: [], run: false}, e: {q: [], run: false}};
const BGQ = BG.x.q;
function bgPush(job, front = false, lane = "x") { const L = BG[lane]; front ? L.q.unshift(job) : L.q.push(job); bgNext(lane); }
function bgNext(lane = "x") {
  const L = BG[lane];
  if (L.run) return;
  if (VOICE.on) { L.q.length = 0; return; }
  if (!L.q.length) return;
  if (BUSY) return void setTimeout(() => bgNext(lane), 1500);
  const j = L.q.shift(); L.run = true;
  let pr; try { pr = Promise.resolve(j()); } catch (_) { pr = Promise.resolve(); }
  pr.catch(() => {}).finally(() => { L.run = false; bgNext(lane); });
}
async function expandClick(turn) {
  Player.stop();
  const x = EXPANDS.get(turn); EXPANDS.delete(turn);
  if (x && !x.started) { const i = BGQ.indexOf(x.job); if (i >= 0) BGQ.splice(i, 1); }  // still queued: ask the usual (streamed) way
  if (!x || !x.started || x.vid !== VID || BUSY || x.r === null) return eventTurn("expand", turn.text, null, true, turn.sources);
  let r = x.r;
  if (r === undefined) {
    BUSY = true;
    const th = skeleton(trStep(EXPAND_STAGES[0][1]));
    const tick = setInterval(() => { const el = th.querySelector(".ptext"); if (!el) return; const sec = Math.round((Date.now() - x.t0) / 1000);
      el.textContent = `${trStep(EXPAND_STAGES.filter(s => sec >= s[0]).pop()[1])} · ${sec} ${t("sec")}`; }, 500);
    try { r = await x.p; } finally { clearInterval(tick); th.remove(); BUSY = false; }
    if (!r) return eventTurn("expand", turn.text, null, true, turn.sources);
  }
  api("/api/chat", {json: {vehicle_id: VID, text: turn.text, event: "expand", ready: {text: r.text, sources: r.sources, sig: r.sig}}}).catch(() => {});  // store it in the history
  return renderTurn(r, true);
}
async function eventTurn(event, text, shown, expanded = false, sources = null) {
  Player.stop();
  if (shown) addMsg("user", esc(shown));
  return ask(text, event, sources);
}
const rawDetails = raw => raw ? `<details class="seen"><summary>${icon("chevron-right", 16)}<span>${t("v_raw")}</span></summary><p class="small muted">${esc(raw)}</p></details>` : "";
async function send(text, {raw = null} = {}) {
  Player.stop();
  text = (text ?? $("#txt").value).trim();
  if (!text) return null;
  if (!VID) {
    const v = plateInText(text); if (!v) { toast(t("choose_vehicle")); return null; }
    await selectVehicle(v.vehicle_id);
  }
  LAST_Q = text; askedAdd(text); $("#txt").value = ""; autosize();
  if (HYP) { HYP = false; $("#txt").placeholder = t("ask_placeholder"); return eventTurn("hypothesis", text, text); }
  addMsg("user", esc(text) + rawDetails(raw));  // voice: what was actually recognised, collapsed
  return ask(text);
}
function autosize() {
  const ta = $("#txt"); ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  const has = !!ta.value.trim();
  $("#send").classList.toggle("hidden", !has);
  $("#mic").classList.toggle("hidden", has);  // voice chat <-> send, like ChatGPT; dictation stays
  layout();
}
function layout() { const c = $("#composer"); if (c) document.documentElement.style.setProperty("--composer-h", (c.classList.contains("hidden") ? 0 : c.offsetHeight) + "px"); }

// ---------------------------------------------------------------- media: photo, video (keyframes + audio track), engine sound
// Everything heavy happens on the phone: a photo is downscaled, a video becomes 4 JPEG keyframes and a 16 kHz mono WAV,
// so a 50 MB clip never travels over the shop Wi-Fi. Results are tied to the vehicle and its history on the server.
function mediaSheet() {
  const item = (id, ic, title, hint) => `<button class="item" id="${id}">${icon(ic)}<span class="t"><b>${t(title)}</b>${hint ? `<span>${t(hint)}</span>` : ""}</span></button>`;
  sheet(t("media_title"), `<div class="list">` + item("m_photo", "camera", "take_photo", "take_photo_hint") + item("m_video", "video", "take_video", "take_video_hint") +
    item("m_sound", "activity", "rec_sound", "rec_sound_hint") + item("m_gal", "image", "from_gallery", "") + `</div>`, sh => {
    sh.querySelector("#m_photo").onclick = () => { closeSheet(); $("#photo").click(); };
    sh.querySelector("#m_video").onclick = () => { closeSheet(); $("#video").click(); };
    sh.querySelector("#m_gal").onclick = () => { closeSheet(); $("#gallery").click(); };
    sh.querySelector("#m_sound").onclick = soundSheet;
  });
}
const toBlob = (cv, q = 0.82) => new Promise(r => cv.toBlob(r, "image/jpeg", q));
function drawScaled(src, w, h, max = 1280) {
  const k = Math.min(1, max / Math.max(w, h)), cv = document.createElement("canvas");
  cv.width = Math.round(w * k); cv.height = Math.round(h * k); cv.getContext("2d").drawImage(src, 0, 0, cv.width, cv.height); return cv;
}
async function shrinkPhoto(f) {
  try { const bm = await createImageBitmap(f); return await toBlob(drawScaled(bm, bm.width, bm.height, 1600)); } catch (_) { return f; }
}
async function videoFrames(f, n = 4) {
  const v = document.createElement("video"); v.muted = true; v.playsInline = true; v.preload = "auto"; v.src = URL.createObjectURL(f);
  await new Promise((ok, bad) => { v.onloadeddata = ok; v.onerror = () => bad(new Error("video")); });
  let d = v.duration;
  if (!isFinite(d)) { v.currentTime = 1e6; await new Promise(r => v.onseeked = r); d = v.currentTime || 1; }
  const out = [];
  for (let i = 0; i < n; i++) {
    const tt = Math.min(d - 0.05, d * (0.1 + 0.8 * i / (n - 1)));
    v.currentTime = Math.max(0, tt); await new Promise(r => v.onseeked = r);
    out.push({t: +tt.toFixed(1), blob: await toBlob(drawScaled(v, v.videoWidth, v.videoHeight))});
  }
  URL.revokeObjectURL(v.src); return {frames: out, duration: d};
}
// any audio or video blob -> 16 kHz mono PCM16 WAV, first 30 s
async function toWav(blob, maxS = 30) {
  const AC = window.AudioContext || window.webkitAudioContext, ac = new AC();
  const buf = await new Promise((ok, bad) => { blob.arrayBuffer().then(ab => { const p = ac.decodeAudioData(ab, ok, bad); if (p && p.then) p.then(ok, bad); }, bad); });
  ac.close && ac.close();
  const sr = 16000, len = Math.max(1, Math.floor(Math.min(buf.duration, maxS) * sr));
  const off = new OfflineAudioContext(1, len, sr), src = off.createBufferSource(); src.buffer = buf; src.connect(off.destination); src.start();
  const pcm = (await off.startRendering()).getChannelData(0), dv = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o, s) => [...s].forEach((c, i) => dv.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); dv.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE"); w(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, sr, true); dv.setUint32(28, sr * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true); w(36, "data"); dv.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) dv.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 32767, true);
  return new Blob([dv], {type: "audio/wav"});
}
async function sendMedia(kind, {frames = [], times = [], audio = null, bubble}) {
  const note = $("#txt").value.trim(); $("#txt").value = ""; autosize();
  addMsg("user", bubble + (note ? `<div style="margin-top:8px">${esc(note)}</div>` : ""));
  const fd = new FormData(); fd.append("kind", kind); fd.append("vehicle_id", VID); fd.append("text", note); fd.append("times", times.join(","));
  frames.forEach((b, i) => fd.append("frames", b, `f${i}.jpg`)); if (audio) fd.append("audio", audio, "a.wav");
  const th = skeleton();
  try {
    const r = await api("/api/media", {method: "POST", body: fd}); th.remove();
    if (r.description) addMsg("bot", `<details class="seen"><summary>${icon("chevron-right", 16)}<span>${t("media_seen")}</span></summary><p class="small muted">${esc(r.description).replace(/\n/g, "<br>")}</p></details>`);
    if (r.plate && r.plate !== VID) {
      const d = addMsg("bot", `<p>${t("vehicle")}: <b class="mono">${esc(r.plate)}</b>?</p><div class="card-actions"><button class="btn primary">${t("open_vehicle")}</button></div>`);
      d.querySelector("button").onclick = () => selectVehicle(r.plate);
    }
    if (r.turn) renderTurn(r.turn);
  } catch (err) { th.remove(); errorMsg(err); }
}
const thumbs = urls => `<div class="thumbs">${urls.map(u => `<img src="${u}" alt="">`).join("")}</div>`;
async function onVideo(f) {
  const th = skeleton(t("media_prep"));
  try {
    const {frames, duration} = await videoFrames(f);
    let audio = null; try { audio = await toWav(f); } catch (_) { toast(t("media_no_audio")); }
    th.remove();
    await sendMedia("video", {frames: frames.map(x => x.blob), times: frames.map(x => x.t), audio,
      bubble: thumbs(frames.map(x => URL.createObjectURL(x.blob))) + `<div class="small muted" style="margin-top:4px">${icon("video", 14)} ${duration.toFixed(0)} ${t("sec")}</div>`});
  } catch (err) { th.remove(); errorMsg(err); }
}
async function onMediaFile(e) {
  const f = e.target.files[0]; e.target.value = ""; if (!f) return;
  if (f.type.startsWith("video/")) return onVideo(f);
  if (!VID) return onPlatePhoto(f);  // no vehicle yet: the photo is used to find it by the plate
  const b = await shrinkPhoto(f);
  return sendMedia("photo", {frames: [b], bubble: `<img src="${URL.createObjectURL(b)}" alt="" class="msg-img">`});
}
async function onPlatePhoto(f) {
  const fd = new FormData(); fd.append("file", await shrinkPhoto(f), "p.jpg"); fd.append("vehicle_id", ""); fd.append("text", "");
  const th = skeleton();
  try {
    const r = await api("/api/photo", {method: "POST", body: fd}); th.remove();
    if (r.plate) selectVehicle(r.plate); else toast(r.description || t("error"));
  } catch (err) { th.remove(); errorMsg(err); }
}
let REC = null;
function soundSheet() {
  sheet(t("rec_sound"), `<p class="small muted">${t("rec_tip")}</p><div class="rec"><canvas id="recLvl" width="320" height="48"></canvas><div class="mono" id="recT">0:00</div></div>` +
    `<div class="card-actions"><button class="btn primary" id="recBtn">${icon("mic", 18)}<span>${t("rec_start")}</span></button></div>`, sh => {
    sh.querySelector("#recBtn").onclick = () => REC ? stopRec() : startRec(sh);
  });
}
async function startRec(sh) {
  let stream;
  // engine sound: no echo cancellation / noise suppression / AGC, they eat exactly the knocks we need
  try { stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false}}); }
  catch (_) { toast(t("mic_denied")); return; }
  const mr = new MediaRecorder(stream), chunks = [], ac = new (window.AudioContext || window.webkitAudioContext)(), an = ac.createAnalyser();
  ac.createMediaStreamSource(stream).connect(an); an.fftSize = 512;
  const data = new Uint8Array(an.fftSize), cv = sh.querySelector("#recLvl"), g = cv.getContext("2d"), t0 = Date.now(), lv = [];
  REC = {mr, stream, ac, timer: null, auto: setTimeout(() => stopRec(), 30000)};
  mr.ondataavailable = e => e.data.size && chunks.push(e.data);
  mr.onstop = async () => {
    stream.getTracks().forEach(x => x.stop()); ac.close(); cancelAnimationFrame(REC.timer); clearTimeout(REC.auto); REC = null; closeSheet();
    const blob = new Blob(chunks, {type: mr.mimeType || "audio/webm"});
    try {
      const wav = await toWav(blob), dur = ((Date.now() - t0) / 1000).toFixed(0);
      await sendMedia("sound", {audio: wav, bubble: `<div class="small">${icon("activity", 14)} ${t("rec_sound")}, ${dur} ${t("sec")}</div><audio controls src="${URL.createObjectURL(wav)}" style="width:100%;margin-top:8px"></audio>`});
    } catch (err) { errorMsg(err); }
  };
  mr.start(250);
  const b = sh.querySelector("#recBtn"); b.classList.replace("primary", "danger"); b.innerHTML = `${icon("square", 18)}<span>${t("rec_stop")}</span>`;
  const draw = () => {
    an.getByteTimeDomainData(data); let m = 0; for (const x of data) m = Math.max(m, Math.abs(x - 128) / 128);
    lv.push(m); if (lv.length > 80) lv.shift();
    g.clearRect(0, 0, cv.width, cv.height); g.fillStyle = getComputedStyle(document.body).getPropertyValue("--accent") || "#5b8cff";
    lv.forEach((v, i) => { const h = Math.max(2, v * cv.height); g.fillRect(i * 4, (cv.height - h) / 2, 3, h); });
    const s = Math.floor((Date.now() - t0) / 1000); sh.querySelector("#recT").textContent = `0:${String(s).padStart(2, "0")}`;
    REC && (REC.timer = requestAnimationFrame(draw));
  };
  draw();
}
function stopRec() { if (REC && REC.mr.state !== "inactive") REC.mr.stop(); }

// ---------------------------------------------------------------- content in the interface language
// Manuals, tickets, chips and history are Russian until people translate the databases. Every Russian text node that
// appears in the chat, a sheet or the viewer is translated via /api/translate (imported translation -> cache -> LLM);
// a translation that loses a number keeps the Russian original. Page text has an «original» toggle.
const OWN = {kk: "әғқңөұүһі", ky: "ңөү", tg: "ғӣқӯҳҷ", uz: "ўқғҳ"};
const needsTr = () => !!(ME && ME.lang && ME.lang !== "ru");
// model names alone (МАЗ-5337, ЯМЗ-236М2) stay as they are
function isRu(x) { if (!/[а-яё]{3,}/.test(String(x))) return false; const own = OWN[ME && ME.lang]; return !(own && [...String(x).toLowerCase()].some(ch => own.includes(ch))); }
const TRC = new Map();
async function trTexts(texts, refs) {
  const lang = ME.lang, need = [...new Set(texts.filter(x => !TRC.has(lang + "\n" + x)))];
  if (need.length) {
    for (let i = 0; i < need.length; i += 40) {
      const part = need.slice(i, i + 40), r = await api("/api/translate", {json: {texts: part, lang, refs: refs && texts.length === 1 ? refs : null}});
      r.items.forEach((it, j) => TRC.set(lang + "\n" + part[j], it));
    }
  }
  return texts.map(x => TRC.get(lang + "\n" + x) || {text: x, src: "failed"});
}
const TR_ASK = new Set();
function trStep(x) {  // progress line: one translation per step text, shown when ready (never blocks the ticker)
  if (!needsTr() || !isRu(x)) return x;
  const hit = TRC.get(ME.lang + "\n" + x); if (hit) return hit.text;
  if (!TR_ASK.has(x)) { TR_ASK.add(x); trTexts([x]).catch(() => {}); }
  return x;
}
let TR_Q = new Set(), TR_T = 0; window.__trBusy = 0;
function trQueue(el) { if (!needsTr() || !el || el.nodeType !== 1) return; TR_Q.add(el); clearTimeout(TR_T); TR_T = setTimeout(trFlush, 120); }
async function trFlush() {
  const roots = [...TR_Q]; TR_Q = new Set();
  const nodes = [];
  for (const root of roots) {
    if (!root.isConnected) continue;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: n => {
      const pe = n.parentElement; if (!pe || pe.closest("[data-notr],textarea,input,script,style,code,.mono,[data-tr-done]")) return 3;
      return n.nodeValue.trim().length > 1 && isRu(n.nodeValue) && !n.__ru ? 1 : 3; }});
    for (let n; (n = w.nextNode());) nodes.push(n);
  }
  if (!nodes.length) return;
  const core = nodes.map(n => n.nodeValue.trim());
  nodes.forEach(n => n.__ru = n.nodeValue);
  window.__trBusy++;
  try {
    const tr = await trTexts(core);
    nodes.forEach((n, i) => { const it = tr[i]; if (it && it.src !== "same" && it.src !== "failed") { const m = n.__ru.match(/^(\s*)[\s\S]*?(\s*)$/); n.nodeValue = m[1] + it.text + m[2]; n.parentElement && n.parentElement.setAttribute("title", n.__ru.trim().slice(0, 300)); } });
  } catch (_) { nodes.forEach(n => delete n.__ru); }
  finally { window.__trBusy--; }
}
const TR_OBS = new MutationObserver(ms => { if (!needsTr()) return; for (const m of ms) m.addedNodes.forEach(n => trQueue(n.nodeType === 1 ? n : n.parentElement)); });
["#chat", "#layer", "#viewer", "#quick", "#vehBtn", "#vehInfo", "#empty"].forEach(sel => { const el = $(sel); if (el) TR_OBS.observe(el, {childList: true, subtree: true}); });

// ---------------------------------------------------------------- citation viewer: extract → full section (collapsed) → page links and image
function hl(txt, q) {
  const words = [...new Set((q || "").toLowerCase().match(/[\p{L}\d]{5,}/gu) || [])].slice(0, 12);
  if (!words.length) return txt;
  const re = new RegExp(`(${words.map(w => w.slice(0, Math.max(5, w.length - 2)).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})[\\p{L}]*`, "giu");
  return txt.replace(re, m => `<mark>${m}</mark>`);
}
// Page viewer: every page link opens in-app (text from the manual index + page image when the stand has it).
// A raw PDF is never the default; «Открыть PDF» is a small secondary link (served inline).
let VIEW = null;
// PDF text layer has hard line breaks: join a line with the next one when the sentence clearly continues.
const reflow = x => String(x || "").replace(/([а-яё])-\n(?=[а-яё])/g, "$1").replace(/([^\n.:;!?])\n(?=[a-zа-яё(«])/g, "$1 ");
// figure captions are separate paragraphs in the page text (moved out of the text column at indexing): show them as captions
const figcaps = h => h.replace(/(^|\n)\n((?:Рис\.|Рисунок)\s*\d[\s\S]*?)(?=\n\n|$)/g, '$1<span class="figcap">$2</span>');
// fault tables come row by row from the index («причина — способ устранения»): one card-like row each, cause over remedy
const TABLE_HEAD = /^(?:Вероятн\S* |Возможн\S* )?(?:Причин|Неисправн)[^\n]{0,40} — (?:Способ|Метод)/m;
const tableRows = h => h.split("\n").map(l => { const k = l.indexOf(" — "); return k > 0 ? `<span class="trow"><span class="tc">${l.slice(0, k)}</span><span class="tr">${l.slice(k + 3)}</span></span>` : `<span class="tsub">${l}</span>`; }).join("");
const pageBody = (raw, q) => TABLE_HEAD.test(raw || "") ? tableRows(hl(esc(raw), q)) : figcaps(hl(esc(reflow(raw)), q));
// The extract is an LLM call (10–16 s on the free model): the first page chips of a new answer are fetched in the background
// while the mechanic reads, so a tap shows it at once. Cached in the client (Vercel instances do not share memory).
const EXTRACTS = new Map();
function getExtract(doc, page, q, question) {
  const k = [doc, +page, (q || "").slice(0, 200), (question || "").slice(0, 300)].join("\u0001");
  if (!EXTRACTS.has(k)) {
    if (EXTRACTS.size > 40) EXTRACTS.delete(EXTRACTS.keys().next().value);
    const p = api("/api/extract", {json: {doc_id: doc, page: +page, q, question}});
    p.catch(() => EXTRACTS.delete(k));
    EXTRACTS.set(k, p);
  }
  return EXTRACTS.get(k);
}
function prefetchExtracts(btns) {
  if (VOICE.on) return;  // hands-free dialog: nobody taps a chip, keep the line free for the next question
  const seen = new Set(), pick = [];
  for (const b of btns) { const k = b.dataset.doc + "#" + b.dataset.page; if (!b.dataset.doc || seen.has(k)) continue; seen.add(k); pick.push(b); if (pick.length >= 2) break; }
  if (!pick.length) return;
  const question = LAST_Q;
  // one at a time in the extract lane, never while a question is being answered (same free model)
  for (const b of pick) bgPush(() => getExtract(b.dataset.doc, +b.dataset.page, b.dataset.q || "", question), false, "e");
}
async function openCitation(doc, page, q) {
  Player.stop(); page = +page;
  $("#viewer").classList.remove("hidden"); document.body.style.overflow = "hidden";
  $("#mTitle").textContent = `${t("page")} ${page}`; $("#mSub").textContent = ""; $("#mPdf").classList.add("hidden");
  $("#mBody").innerHTML = `<div class="extract skeleton"><div class="line"></div><div class="line"></div><div class="line"></div></div>`;
  try {
    const s = await api(`/api/section/${encodeURIComponent(doc)}/${page}?q=${encodeURIComponent(q || LAST_Q)}`);
    const texts = Object.fromEntries(s.pages.map(p => [p.page, p.text]));
    const pages = [...new Set([...s.pages.map(p => p.page), page])].sort((a, b) => a - b);
    VIEW = {doc, s, q, pages, texts, cur: page};
    $("#mTitle").textContent = s.label || `${t("page")} ${page}`; $("#mSub").textContent = s.full_title;
    $("#mBody").innerHTML = (s.extra ? `<p class="small muted">* ${t("extra_src")}</p>` : "") +
      `<div class="extract" id="mExtract"><div class="h">${t("extract")}, ${t("page_short")} ${page}</div><div class="skeleton"><div class="line"></div><div class="line"></div><div class="line"></div></div></div>` +
      `<div class="label">${t("pages")}</div><div class="pages" id="mPages">${pages.map(n => `<button data-pg="${n}">${t("page_short")} ${n}</button>`).join("")}</div>` +
      `<section class="pageview" id="mPage" aria-live="polite"></section>` +
      `<div class="pnav"><button class="btn ghost sm" id="mPrev">${icon("chevron-left", 18)}<span></span></button><button class="btn ghost sm" id="mNext"><span></span>${icon("chevron-right", 18)}</button></div>` +
      (pages.length > 1 ? `<details class="sec"><summary>${icon("chevron-right", 18)}<span>${t("full_section")}, ${t("page_short")} ${esc(s.span)}</span></summary><div class="in">` +
        s.pages.map(p => `<div class="pagehead">${t("page")} ${p.page}</div><div class="pagetext">${pageBody(p.text, q)}</div>`).join("") + `</div></details>` : "") +
      (s.pdf || s.pdf_text ? `<p class="pdflink"><a id="mPdfLink" target="_blank" rel="noopener">${icon("external-link", 14)}${t(s.pdf ? "open_pdf" : "open_pdf_text")}</a></p>` : "");
    $("#mPages").querySelectorAll("[data-pg]").forEach(b => b.onclick = () => showPage(+b.dataset.pg, true));
    $("#mPrev").onclick = () => showPage(VIEW.pages[VIEW.pages.indexOf(VIEW.cur) - 1], true);
    $("#mNext").onclick = () => showPage(VIEW.pages[VIEW.pages.indexOf(VIEW.cur) + 1], true);
    await showPage(page, false);
    getExtract(doc, page, q, LAST_Q).then(x => {
      const box = $("#mExtract"); if (!box) return;
      box.innerHTML = `<div class="h">${t("extract")}, ${t("page_short")} ${page}</div>` + (x.ok ? md(x.text) : `<p class="muted">${t("extract_na")}</p>`);
    }).catch(() => { const box = $("#mExtract"); if (box) box.innerHTML = `<div class="h">${t("extract")}</div><p class="muted">${t("extract_na")}</p>`; });
  } catch (err) { $("#mBody").innerHTML = `<p class="err">${esc(friendlyError(err))}</p>`; }
}
async function showPage(n, scroll) {
  if (!VIEW || n == null) return;
  const {doc, s, q, pages} = VIEW; VIEW.cur = n;
  $("#mPages").querySelectorAll("[data-pg]").forEach(b => b.classList.toggle("on", +b.dataset.pg === n));
  const i = pages.indexOf(n), prev = pages[i - 1], next = pages[i + 1];
  $("#mPrev").disabled = prev == null; $("#mPrev").querySelector("span").textContent = prev == null ? t("page_short") + " —" : `${t("page_short")} ${prev}`;
  $("#mNext").disabled = next == null; $("#mNext").querySelector("span").textContent = next == null ? t("page_short") + " —" : `${t("page_short")} ${next}`;
  const box = $("#mPage");
  box.innerHTML = `<div class="pv-head">${t("page")} ${n}<span class="muted"> · ${i + 1} / ${pages.length}</span></div><div class="skeleton"><div class="line"></div><div class="line"></div></div>`;
  if (scroll) box.scrollIntoView({behavior: "smooth", block: "start"});
  let txt = VIEW.texts[n], img = s.has_image;
  if (txt == null) { try { const p = await api(`/api/page/${encodeURIComponent(doc)}/${n}`); txt = VIEW.texts[n] = p.text; img = p.has_image; } catch (_) { txt = ""; } }
  if (VIEW.cur !== n) return;
  const trOn = needsTr() && txt && isRu(txt);
  box.innerHTML = `<div class="pv-head">${t("page")} ${n}<span class="muted"> · ${i + 1} / ${pages.length}</span>` +
    (trOn ? `<button class="origbtn" id="mOrig" aria-pressed="false">${t("show_original")}</button>` : "") + `</div>` +
    `<div class="pagetext" data-notr>${txt ? pageBody(txt, q) : `<span class="muted">${t("page_no_text")}</span>`}</div>` +
    (img ? `<img class="pageimg" id="mImg" loading="lazy" src="/page/${encodeURIComponent(doc)}/${n}.png" alt="${esc(t("page_image"))} ${n}">` : "");
  const a = $("#mPdfLink"); if (a) a.href = `/manuals/${encodeURIComponent(doc)}.pdf?page=${n}#page=${n}`;
  if (trOn) {
    const el = box.querySelector(".pagetext"), orig = el.innerHTML, ob = $("#mOrig");
    el.classList.add("tr-wait");
    const tr = await trTexts([reflow(txt)], [{doc_id: doc, page: n}]).catch(() => null);
    if (VIEW.cur !== n) return;
    el.classList.remove("tr-wait");
    if (!tr || tr[0].src === "same" || tr[0].src === "failed") { ob.remove(); return; }
    const trHtml = hl(esc(tr[0].text), q);
    let showOrig = !!VIEW.orig;
    const paint = () => { el.innerHTML = showOrig ? orig : trHtml; el.lang = showOrig ? "ru" : ME.lang; ob.setAttribute("aria-pressed", showOrig); ob.textContent = showOrig ? t("show_translation") : t("show_original"); };
    ob.onclick = () => { showOrig = VIEW.orig = !showOrig; paint(); };
    paint();
  }
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
const VOICE = {on: false, browser: false, srSeq: 0, state: "idle", stream: null, ctx: null, an: null, rec: null, chunks: [], raf: 0, heard: false, quietMs: 0, speechMs: 0, t0: 0, last: 0, floor: 4, wave: []};
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
  $("#voice").classList.remove("hidden"); $("#vSaid").textContent = ""; $("#vReply").textContent = ""; VOICE.on = true; VOICE.wave = [];
  VOICE.browser = !SERVER_STT;
  if (VOICE.browser && !WebSTT.available()) { vSet("idle", WebSTT.message(null, t)); return; }
  try {
    VOICE.stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
  } catch (e) { vSet("idle", t("v_no_mic")); return; }
  VOICE.ctx = new (window.AudioContext || window.webkitAudioContext)();
  VOICE.an = VOICE.ctx.createAnalyser(); VOICE.an.fftSize = 1024;
  VOICE.ctx.createMediaStreamSource(VOICE.stream).connect(VOICE.an);
  meter(); vListen();
}
// no server STT (Vercel: 503) -> the browser recognises the phrase (Web Speech API, language of the UI)
function vListenBrowser(hint = "") {
  if (!VOICE.on) return;
  Player.stop(); VOICE.speechMs = 0;
  if (!WebSTT.available()) return vSet("idle", WebSTT.message(null, t));
  const my = ++VOICE.srSeq;
  vSet("listening", hint);
  WebSTT.listen(SPEECH_LANG[(ME && ME.lang) || "ru"] || "ru-RU", {onInterim: s => { if (my === VOICE.srSeq && s) $("#vSaid").textContent = s; }})
    .then(text => { if (my !== VOICE.srSeq || !VOICE.on) return; if (!text) return vSet("idle", t("v_tap_to_talk")); vHeard(text); })
    .catch(e => { if (my === VOICE.srSeq && VOICE.on) vSet("idle", WebSTT.message(e, t)); });
}
function rms() { const buf = new Uint8Array(VOICE.an.fftSize); VOICE.an.getByteTimeDomainData(buf); let s = 0; for (const v of buf) s += (v - 128) ** 2; return Math.sqrt(s / buf.length); }
function drawVoiceWave(level) {
  const cv = $("#vWave"); if (!cv) return;
  const box = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (box.width && Math.abs(cv.width - box.width * dpr) > 2) { cv.width = box.width * dpr; cv.height = box.height * dpr; }
  const g = cv.getContext("2d"), w = cv.width, h = cv.height; if (!w || !h) return;
  const active = VOICE.state === "listening" || VOICE.state === "speaking";
  VOICE.wave.push(active ? level : 0); while (VOICE.wave.length > 56) VOICE.wave.shift();
  g.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.documentElement);
  g.fillStyle = (VOICE.state === "speaking" ? css.getPropertyValue("--ok") : css.getPropertyValue("--accent")).trim() || "#4f8cff";
  const step = w / 56, bw = Math.max(2 * dpr, step * 0.48), mid = h / 2;
  for (let i = 0; i < 56; i++) {
    const v = VOICE.wave[i] || 0, bh = Math.max(2.2 * dpr, Math.pow(v, 0.65) * h * 0.88), x = i * step + (step - bw) / 2;
    g.globalAlpha = v > 0.03 ? 0.95 : 0.28;
    g.fillRect(x, mid - bh / 2, bw, bh);
  }
  g.globalAlpha = 1;
}
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
  const r = rms(), lvl = Math.min(1, r / 30); $("#vOrb").style.setProperty("--lvl", lvl.toFixed(2)); drawVoiceWave(lvl);
  if (VOICE.state === "listening" && !VOICE.browser) {  // browser recognition has its own end-of-phrase detection
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
  if (VOICE.browser) return vListenBrowser();
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
  let blob = new Blob(VOICE.chunks, {type: (VOICE.rec && VOICE.rec.mimeType) || "audio/webm"}), name = "voice.webm"; VOICE.rec = null;
  if (!VOICE.on) return;
  if (blob.size < 1500) return vListen();
  try { blob = await toWav(blob, 15); name = "voice.wav"; } catch (_) {}  // NVIDIA/whisper need WAV; keep the original blob if the browser cannot decode it
  const fd = new FormData(); fd.append("audio", blob, name); fd.append("lang", ME.lang || "ru"); fd.append("vehicle_id", VID || "");
  let text = "", intent;
  try { const r = await api("/api/stt", {method: "POST", body: fd}); text = (r.text || "").trim(); intent = r.intent; }
  catch (e) {
    if (!VOICE.on) return;
    if (WebSTT.serverFailed(e)) {  // 503 «STT unavailable» or no network to the server: switch to the browser for the rest of the session
      SERVER_STT = false; VOICE.browser = true;
      return WebSTT.available() ? vListenBrowser(t("v_browser_stt")) : vSet("idle", WebSTT.message(null, t));
    }
    vSet("idle", friendlyError(e)); return;
  }
  if (!VOICE.on) return;
  if (!text) { vSet("listening", t("v_didnt_hear")); return vListen(); }
  vHeard(text, intent);
}
// Voice screen text: whole sentences up to n chars (was a cut mid-word «двигатель переби…», audit 01:20, 390 px)
function clipText(s, n) {
  if (s.length <= n) return s;
  const head = s.slice(0, n); let end = -1;  // a sentence end, not «стр. 53» / «с. 52» / «т. д.»
  for (const m of head.matchAll(/(^|[\s(])(?:стр|с|рис|табл|т|д|п|ч|им|см|прим)\.(?=\s)|[.!?](?=\s)|\n/g)) if (m[0].length === 1) end = m.index;  // abbreviations match longer and are skipped
  return end > n * 0.4 ? head.slice(0, end + 1).trim() + " …" : head.slice(0, head.lastIndexOf(" ")).trim() + "…";
}
// Noisy workshop (V t565u): the server reconstructs the mechanic's repair phrase from a mixed transcript; low confidence ->
// «Я понял так: … Верно?» and the next phrase answers it (да / нет); off-topic only -> ask again. Numbers are never added.
const V_YES = /^\s*(да|верно|ага|так|правильно|точно|подтверждаю|yes|ha|иә|ҳа|ооба|हाँ|có|হ্যাঁ)\b/i, V_NO = /^\s*(нет|не так|неверно|не то|no|yo['ʻ]q|жоқ|жок|नहीं|không|না)\b/i;
async function vIntent(raw) {
  if (!VID) return null;
  try { return (await api("/api/voice/intent", {json: {vehicle_id: VID, text: raw}})).intent; } catch (_) { return null; }
}
async function vHeard(raw, intent) {
  if (VOICE.pending) {
    const p = VOICE.pending; VOICE.pending = null;
    if (V_YES.test(raw)) return vSendHeard(p.utterance, p.raw);
    if (V_NO.test(raw)) { $("#vSaid").textContent = ""; vSet("listening", t("v_repeat")); return vListen(); }
  }
  if (intent === undefined) intent = await vIntent(raw);
  if (!VOICE.on) return;
  if (intent && intent.offtopic) { $("#vSaid").innerHTML = esc(raw); vSet("listening", t("v_offtopic")); return vListen(); }
  if (intent && intent.confirm && intent.utterance) {
    VOICE.pending = {utterance: intent.utterance, raw};
    const q = t("v_understood").replace("{x}", intent.utterance);
    $("#vSaid").innerHTML = esc(q) + rawDetails(raw); $("#vReply").textContent = "";
    if (!A11Y.voiceSpeaks()) { vSet("listening"); return vListen(); }
    vSet("speaking"); VOICE.speechMs = 0;
    return Player.play(q, null, {lang: speakLang(), original: q, onend: () => { if (VOICE.on && VOICE.state === "speaking") vListen(); }});
  }
  const text = intent && intent.utterance ? intent.utterance : raw;
  return vSendHeard(text, text !== raw ? raw : null);
}
// Hands-free on Vercel waits 15–30 s for the free model: the verified lines streamed so far show dimmed under the phrase,
// so the mechanic sees the answer being written; the final answer replaces them (and is what gets spoken).
function vDraft(draft) {
  if (!VOICE.on || VOICE.state !== "thinking" || !draft) return;
  const el = $("#vReply"); el.textContent = clipText(plain(draft), 600); el.classList.add("draft"); el.classList.toggle("long", el.textContent.length > 140);
}
async function vSendHeard(text, raw) {
  $("#vSaid").innerHTML = esc(text) + rawDetails(raw); $("#vReply").textContent = ""; $("#vReply").classList.remove("long", "draft");
  vSet("thinking");
  // Another answer still in flight (an explanation after «Получить результат», a chip): ask() drops a request while BUSY and the
  // dialog said «Ответа нет» 1 s after the phrase (Vercel audit 27.11 10:50). Wait for it (up to 90 s), then ask.
  for (const tw = Date.now(); BUSY && VOICE.on && Date.now() - tw < 90000;) await new Promise(r => setTimeout(r, 300));
  if (!VOICE.on) return;
  const turn = await send(text, {raw});
  if (!VOICE.on) return;
  const reply = turn && (turn.text || (turn.cards || []).map(c => c.light_text || "").join(" "));
  $("#vReply").classList.remove("draft");
  if (!reply) { $("#vReply").textContent = ""; vSet("idle", t("v_no_answer")); return; }
  $("#vReply").textContent = clipText(plain(reply), 600);
  $("#vReply").classList.toggle("long", plain(reply).length > 140); $("#vReply").scrollTop = 0;
  if (!A11Y.voiceSpeaks()) return vListen();
  vSet("speaking"); VOICE.speechMs = 0;
  Player.play(reply, null, {lang: speakLang(), original: reply, onend: () => { if (VOICE.on && VOICE.state === "speaking") vListen(); }});
}
function voiceClose() {
  VOICE.on = false; VOICE.pending = null; VOICE.srSeq++; WebSTT.abort(); cancelAnimationFrame(VOICE.raf); stopRecorder(); Player.stop();
  try { VOICE.stream && VOICE.stream.getTracks().forEach(tr => tr.stop()); } catch (_) {}
  try { VOICE.ctx && VOICE.ctx.close(); } catch (_) {}
  VOICE.stream = VOICE.ctx = VOICE.an = null; VOICE.last = 0;
  $("#voice").classList.add("hidden");
}
function orbTap() {
  if (VOICE.state === "listening") VOICE.browser ? WebSTT.stop() : vStopRec();
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
    `<div class="field"><label class="label" for="s_country">${t("country")}</label><select id="s_country" class="input"></select><p class="small muted" id="s_law" style="margin-top:8px"></p></div>` +
    `<div class="field"><span class="label">${t("level")}</span>${segHtml("s_exp", [["novice", short(t("exp_novice"))], ["experienced", short(t("exp_expert"))], ["expert", short(t("exp_pro"))]], ME.experience || "experienced")}</div>` +
    `<div class="field"><span class="label">${t("detail")}</span>${segHtml("s_detail", [["brief", t("detail_brief")], ["detailed", t("detail_detailed")]], ME.detail || "brief")}</div>` +
    `<div class="field"><span class="label">${t("font_size")}</span>${segHtml("s_fs", [["n", "A"], ["l", "A+"], ["xl", "A++"]], A11Y.size())}</div>` +
    `<div class="field"><span class="label">${t("theme")}</span>${segHtml("s_theme", [["dark", t("theme_dark")], ["light", t("theme_light")], ["contrast", t("theme_contrast")]], A11Y.theme())}</div>` +
    `<label class="switch"><span>${t("voice_speaks")}</span><input type="checkbox" id="s_vtts" ${A11Y.voiceSpeaks() ? "checked" : ""}></label>` +
    `<label class="switch"><span>${t("captions")}</span><input type="checkbox" id="s_cc" ${A11Y.captions() ? "checked" : ""}></label>` +
    `<label class="switch"><span>${t("tap_sounds")}</span><input type="checkbox" id="s_snd" ${A11Y.sounds() ? "checked" : ""}></label>` +
    `<div class="sep"></div><div class="field"><span class="label">${t("style_title")}</span><div id="s_style">${t("loading")}</div></div>` +
    `<div class="sep"></div><div class="field"><span class="label">${t("my_profile")}</span><div id="s_prof" class="small muted">${t("loading")}</div></div>` +
    (ME.login || ME.chief || /^demo-/.test(ME.id || "") ? `<div class="sep"></div><div class="field"><span class="label" id="s_qr_t"></span><div id="s_qr"></div></div>` : "") +
    `<div class="sep"></div><p class="small muted">${t("privacy_note")} <a href="/privacy?country=${esc(ME.country || "RU")}" target="_blank">${t("policy")}</a></p>` +
    `<div class="card-actions"><button class="btn ghost" id="s_logout">${icon("log-out", 18)}<span>${t("logout")}</span></button><button class="btn danger" id="s_delete">${t("delete_data")}</button></div>`, sh => {
    if (sh.querySelector("#s_qr") && window.QRLink) { QRLink.mount(sh.querySelector("#s_qr"), {email: ME.email, lang: ME.lang}); QRLink.t("title", ME.lang).then(x => sh.querySelector("#s_qr_t").textContent = x); }
    langOptions(sh.querySelector("#s_lang"), ME.lang); langOptions(sh.querySelector("#s_alang"), ME.answer_lang || "", true);
    sh.querySelector("#s_lang").onchange = async e => { await saveProfile({lang: e.target.value}); settingsSheet(); if (VID) loadChips(); else showEmpty(); };
    sh.querySelector("#s_alang").onchange = e => saveProfile({answer_lang: e.target.value});
    loadCountries().then(() => { const sel = sh.querySelector("#s_country"); countryOptions(sel, ME.country || "RU");
      const law = () => { const c = COUNTRIES.find(x => x.code === sel.value); sh.querySelector("#s_law").textContent = c ? `${c.law}. ${c.localize ? t("stt_local") : ""}` : ""; };
      law(); sel.onchange = async () => { await saveProfile({country: sel.value}); law(); }; });
    drawProfile(sh); drawStyle(sh);
    bindSeg(sh, "s_exp", v => saveProfile({experience: v}));
    bindSeg(sh, "s_detail", v => saveProfile({detail: v}));
    bindSeg(sh, "s_fs", v => { A11Y.setSize(v); layout(); });
    bindSeg(sh, "s_theme", v => A11Y.setTheme(v));
    sh.querySelector("#s_vtts").onchange = e => A11Y.setVoiceSpeaks(e.target.checked);
    sh.querySelector("#s_cc").onchange = e => A11Y.setCaptions(e.target.checked);
    sh.querySelector("#s_snd").onchange = e => { A11Y.setSounds(e.target.checked); A11Y.sound("tap"); };
    sh.querySelector("#s_logout").onclick = async () => { await api("/api/logout", {method: "POST"}); location.reload(); };
    sh.querySelector("#s_delete").onclick = async () => { if (confirm(t("delete_confirm"))) { await api("/api/me", {method: "DELETE"}); location.reload(); } };
  });
}
// ---------------------------------------------------------------- onboarding: pick how the assistant talks (seeds the style card)
function onboarding() {
  return new Promise(async done => {
    let o; try { o = await api("/api/onboarding"); } catch (_) { return done(); }
    if (!o.needed) return done();
    const pick = {formality: "informal", detail: o.detail || "brief", experience: o.experience || "experienced"};
    sheet(t("ob_title"),
      `<p class="small muted">${t("ob_intro")}</p><p class="ob-q"><b>${esc(o.sample.question)}</b></p>` +
      `<div class="ob-opts">` + ["informal", "formal"].map(k => `<button type="button" class="ob-opt ${k === pick.formality ? "on" : ""}" data-f="${k}"><span class="label">${t("ob_" + k)}</span><span>${esc(o.sample[k])}</span></button>`).join("") + `</div>` +
      `<div class="field"><span class="label">${t("detail")}</span>${segHtml("ob_detail", [["brief", t("detail_brief")], ["detailed", t("detail_detailed")]], pick.detail)}</div>` +
      `<div class="field"><span class="label">${t("level")}</span>${segHtml("ob_exp", [["novice", short(t("exp_novice"))], ["experienced", short(t("exp_expert"))], ["expert", short(t("exp_pro"))]], pick.experience)}</div>` +
      `<p class="small muted">${t("ob_later")}</p><div class="card-actions"><button class="btn primary block" id="ob_go">${t("ob_go")}</button></div>`, sh => {
      sh.querySelectorAll(".ob-opt").forEach(b => b.onclick = () => { pick.formality = b.dataset.f; sh.querySelectorAll(".ob-opt").forEach(x => x.classList.toggle("on", x === b)); });
      bindSeg(sh, "ob_detail", v => pick.detail = v); bindSeg(sh, "ob_exp", v => pick.experience = v);
      const c = sh.querySelector("#shClose"); if (c) c.classList.add("hidden");
      sh.querySelector("#ob_go").onclick = async () => { try { ME = (await api("/api/onboarding", {json: pick})).user; } catch (e) { return toast(friendlyError(e)); } closeSheet(); done(); };
    });
  });
}
async function loadAssignments() {
  if (!ME || ME.role !== "mechanic") return;
  let r; try { r = await api("/api/my/assignments"); } catch (_) { return; }
  const old = $("#assignBar"); if (old) old.remove();
  if (!r.assignments.length) return;
  const a = r.assignments[0], bar = document.createElement("button");
  bar.id = "assignBar"; bar.className = "assign-bar";
  bar.innerHTML = `${icon("clipboard-check", 18)}<span><b>${t("assigned_to_you")}: ${esc(a.ticket_id)}, ${esc(a.vehicle_id)}</b><span class="small">${esc((a.summary || "").slice(0, 120))}</span></span>`;
  bar.onclick = () => { localStorage.setItem("vid", a.vehicle_id); VID = a.vehicle_id; location.reload(); };
  $("#main").prepend(bar);
}
// «Как помощник с вами говорит»: versioned style card, editable; memory callbacks on/off
async function drawStyle(sh) {
  const box = sh.querySelector("#s_style"); if (!box) return;
  let st; try { st = (await api("/api/me")).style; } catch (_) { return; }
  const src = {seed: t("style_src_seed"), onboarding: t("style_src_onboarding"), model: t("style_src_model"), user: t("style_src_user")}[st.source] || "";
  box.innerHTML = `<textarea id="s_style_t" class="input" rows="4">${esc(st.text)}</textarea><p class="small muted" style="margin-top:8px">v${st.v}${src ? ", " + esc(src) : ""}. ${t("style_note")}</p>` +
    `<div class="card-actions" style="justify-content:flex-start"><button class="btn primary sm" id="s_style_save">${t("save")}</button><button class="btn ghost sm" id="s_style_reset">${t("prof_reset")}</button></div>` +
    `<label class="switch"><span>${t("memory_callbacks")}</span><input type="checkbox" id="s_mem" ${st.memory ? "checked" : ""}></label>`;
  box.querySelector("#s_style_save").onclick = async () => { await api("/api/me/style", {json: {text: box.querySelector("#s_style_t").value}}); toast(t("saved")); drawStyle(sh); };
  box.querySelector("#s_style_reset").onclick = async () => { await api("/api/me/style", {json: {reset: true}}); drawStyle(sh); };
  box.querySelector("#s_mem").onchange = e => api("/api/me/style", {json: {memory: e.target.checked}});
}
const ABBR = new Set(["гбц", "цпг", "кшм", "грм", "тнвд"]);
// What the assistant learned about the user (agent/profile.py): visible, editable, deletable.
async function drawProfile(sh, prof) {
  const box = sh.querySelector("#s_prof"); if (!box) return;
  try { prof = prof || (await api("/api/me")).profile; } catch (_) { box.textContent = ""; return; }
  const lvl = {novice: t("exp_novice"), experienced: t("exp_expert"), expert: t("exp_pro")}[prof.inferred];
  box.innerHTML = `<p>${t("role")}: <b>${esc(ME.role === "manager" ? t("role_manager") : t("role_mechanic"))}</b>. ${t("prof_messages")}: ${prof.messages}.` +
    (lvl ? ` ${t("prof_inferred")}: <b>${esc(short(lvl))}</b>.` : "") + (prof.prefers_detail ? ` ${t("prof_prefers_detail")}` : "") + `</p>` +
    `<p style="margin-top:8px">${t("prof_terms")}:</p><div class="terms">` +
    (prof.terms.length ? prof.terms.map(k => `<button class="term" data-k="${esc(k)}" aria-label="${esc(t("remove"))} ${esc(k)}">${esc(ABBR.has(k) ? k.toUpperCase() : k)} ${icon("x", 14)}</button>`).join("") : `<span>${t("prof_no_terms")}</span>`) + `</div>` +
    `<button class="btn ghost sm" id="s_preset" style="margin-top:12px">${icon("rotate-ccw", 16)}<span>${t("prof_reset")}</span></button>`;
  box.querySelectorAll(".term").forEach(b => b.onclick = async () => drawProfile(sh, (await api("/api/me/profile", {json: {remove_term: b.dataset.k}})).profile));
  box.querySelector("#s_preset").onclick = async () => drawProfile(sh, (await api("/api/me/profile", {json: {reset: true}})).profile);
}
async function saveProfile(patch) { try { ME = (await api("/api/me", {json: patch})).user; applyI18n(); } catch (e) { toast(friendlyError(e)); } }

// ---------------------------------------------------------------- dictation into the composer: messenger-style record / cancel / accept, not hands-free chat
const DICT = {on: false, seq: 0, mode: "", stream: null, ctx: null, an: null, rec: null, chunks: [], raf: 0, tick: 0, auto: 0, t0: 0, start: "", prefix: "", wave: [], blob: null, name: ""};
function setDictateUi(on) {
  const b = $("#dictate"); if (!b) return;
  b.classList.toggle("on", on);
  b.innerHTML = icon(on ? "square" : "mic", 18);
  b.setAttribute("aria-label", on ? t("stop") : t("dictate"));
  b.title = b.getAttribute("aria-label");
}
function dictBox() { return $(".inputbox"); }
function dictTimerText() {
  const sec = Math.max(0, Math.floor((performance.now() - DICT.t0) / 1000));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}
function drawDictWave(level) {
  const cv = $("#dictWave"); if (!cv) return;
  const box = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (box.width && Math.abs(cv.width - box.width * dpr) > 2) { cv.width = box.width * dpr; cv.height = box.height * dpr; }
  const g = cv.getContext("2d"), w = cv.width, h = cv.height; if (!w || !h) return;
  DICT.wave.push(level); while (DICT.wave.length > 48) DICT.wave.shift();
  g.clearRect(0, 0, w, h);
  g.fillStyle = (getComputedStyle(document.documentElement).getPropertyValue("--danger") || "#e5484d").trim();
  const step = w / 48, bw = Math.max(2 * dpr, step * 0.46), mid = h / 2;
  for (let i = 0; i < 48; i++) {
    const v = DICT.wave[i] || 0, bh = Math.max(2 * dpr, Math.pow(Math.min(1, v), 0.65) * h * 0.86), x = i * step + (step - bw) / 2;
    g.globalAlpha = v > 0.025 ? 0.95 : 0.32;
    g.fillRect(x, mid - bh / 2, bw, bh);
  }
  g.globalAlpha = 1;
}
function dictRms() {
  if (!DICT.an) return 0.06 + 0.03 * Math.sin(performance.now() / 180);  // browser-STT fallback owns the mic itself: show activity, not silence
  const buf = new Uint8Array(DICT.an.fftSize); DICT.an.getByteTimeDomainData(buf);
  let sum = 0; for (const v of buf) sum += (v - 128) ** 2;
  return Math.min(1, Math.sqrt(sum / buf.length) / 28);
}
function dictMeter() {
  if (!DICT.on) return;
  drawDictWave(dictRms());
  DICT.raf = requestAnimationFrame(dictMeter);
}
function dictUiStart() {
  const box = dictBox(); if (!box) return;
  DICT.wave = []; DICT.t0 = performance.now();
  box.classList.add("recording"); $("#dictRec").classList.remove("transcribing", "error");
  $("#dictDone").innerHTML = icon("check", 18);
  $("#dictTime").textContent = "0:00";
  clearInterval(DICT.tick); DICT.tick = setInterval(() => { const el = $("#dictTime"); if (el) el.textContent = dictTimerText(); }, 250);
  cancelAnimationFrame(DICT.raf); dictMeter();
}
function dictUiDone(transcribing = false) {
  cancelAnimationFrame(DICT.raf); clearInterval(DICT.tick); clearTimeout(DICT.auto);
  if (transcribing) {
    $("#dictRec").classList.remove("error");
    $("#dictRec").classList.add("transcribing");
    $("#dictDone").innerHTML = icon("loader-circle", 18, "spin");
    return;
  }
  const box = dictBox(); if (box) box.classList.remove("recording");
  const rec = $("#dictRec"); if (rec) rec.classList.remove("transcribing", "error");
  const done = $("#dictDone"); if (done) done.innerHTML = icon("check", 18);
}
function dictReleaseMic() {
  try { DICT.stream && DICT.stream.getTracks().forEach(tr => tr.stop()); } catch (_) {}
  try { DICT.ctx && DICT.ctx.close(); } catch (_) {}
  DICT.stream = DICT.ctx = DICT.an = null;
}
function dictInsert(text) {
  const ta = $("#txt");
  ta.value = (DICT.prefix + text).replace(/\s+/g, " ").replace(/^\s+/, "");
  autosize(); ta.focus();
}
function dictReset() {
  DICT.on = false; DICT.mode = ""; DICT.rec = null; DICT.chunks = []; DICT.blob = null; DICT.name = "";
  setDictateUi(false); dictUiDone(false);
}
async function dictateServerStart() {
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}}); }
  catch (_) { toast(t("mic_denied")); return; }
  const ta = $("#txt"), my = ++DICT.seq;
  DICT.on = true; DICT.mode = "server"; DICT.stream = stream; DICT.chunks = [];
  DICT.start = ta.value; DICT.prefix = DICT.start && !/\s$/.test(DICT.start) ? DICT.start + " " : DICT.start;
  try {
    DICT.ctx = new (window.AudioContext || window.webkitAudioContext)();
    DICT.an = DICT.ctx.createAnalyser(); DICT.an.fftSize = 512;
    DICT.ctx.createMediaStreamSource(stream).connect(DICT.an);
  } catch (_) { DICT.ctx = DICT.an = null; }
  let mr;
  try {
    mr = new MediaRecorder(stream); DICT.rec = mr;
    mr.ondataavailable = e => e.data.size && DICT.chunks.push(e.data);
    mr.onstop = () => dictationTranscribe(my);
    mr.start(250);
  } catch (_) {
    dictReleaseMic(); dictReset(); toast(t("mic_denied")); return;
  }
  setDictateUi(true); dictUiStart();
  DICT.auto = setTimeout(() => { if (my === DICT.seq) dictFinish(); }, 60000);
}
async function dictationTranscribe(my) {
  if (my !== DICT.seq) return;
  const mr = DICT.rec, chunks = DICT.chunks.slice();
  dictUiDone(true); dictReleaseMic();
  let blob = new Blob(chunks, {type: (mr && mr.mimeType) || "audio/webm"}), name = "dictation.webm";
  if (blob.size < 1200) { dictReset(); return; }
  try { blob = await toWav(blob, 30); name = "dictation.wav"; } catch (_) {}
  if (my !== DICT.seq) return;  // cancelled while the browser was converting the recording: do not send the audio
  DICT.blob = blob; DICT.name = name;
  return dictationUpload(my);
}
function dictShowError(e, msg) {
  const rec = $("#dictRec"), done = $("#dictDone");
  if (rec) rec.classList.remove("transcribing"), rec.classList.add("error");
  if (done) {
    done.innerHTML = icon("rotate-ccw", 18);
    done.setAttribute("aria-label", t("retry")); done.title = t("retry");
  }
  toast(msg || friendlyError(e));
}
async function dictationUpload(my) {
  if (my !== DICT.seq || !DICT.blob) return;
  dictUiDone(true);
  const fd = new FormData(); fd.append("audio", DICT.blob, DICT.name || "dictation.webm"); fd.append("lang", (ME && ME.lang) || "ru"); fd.append("vehicle_id", VID || "");
  try {
    const r = await api("/api/stt", {method: "POST", body: fd});
    if (my !== DICT.seq) return;
    const text = (r.text || "").trim();
    if (!text) { dictShowError(null, t("v_didnt_hear")); return; }  // same recording can be retried; nothing was inserted
    dictReset();
    dictInsert(text);
  } catch (e) {
    if (my !== DICT.seq) return;
    if (WebSTT.serverFailed(e)) SERVER_STT = false;
    dictShowError(e);
  }
}
async function dictateBrowserStart() {
  const ta = $("#txt"), my = ++DICT.seq;
  DICT.on = true; DICT.mode = "browser"; DICT.start = ta.value;
  DICT.prefix = DICT.start && !/\s$/.test(DICT.start) ? DICT.start + " " : DICT.start;
  setDictateUi(true); dictUiStart();
  const lang = SPEECH_LANG[(ME && ME.lang) || "ru"] || "ru-RU";
  try {
    const text = await WebSTT.listen(lang, {onInterim: x => {
      if (my !== DICT.seq) return;
      ta.value = (DICT.prefix + x).replace(/\s+/g, " ");
      autosize();
    }});
    if (my !== DICT.seq) return;
    dictReset();
    if (text) dictInsert(text);
  } catch (e) {
    if (my === DICT.seq) { dictReset(); toast(WebSTT.message(e, t)); }
  }
}
function dictFinish() {
  if (!DICT.on) return;
  if (DICT.blob) return dictationUpload(DICT.seq);  // retry after a network/STT failure: the recording is still local
  if (DICT.mode === "browser") return WebSTT.stop();
  if (DICT.rec && DICT.rec.state !== "inactive") DICT.rec.stop();
}
function dictateCancel() {
  if (!DICT.on) return;
  const mode = DICT.mode;
  DICT.seq++;
  if (mode === "server") {
    if (DICT.rec && DICT.rec.state !== "inactive") {
      DICT.rec.ondataavailable = null; DICT.rec.onstop = null;
      try { DICT.rec.stop(); } catch (_) {}
    }
    dictReleaseMic();
  } else {
    WebSTT.abort();
    $("#txt").value = DICT.start; autosize();
  }
  dictReset();
}
async function dictateToggle() {
  if (DICT.on) return dictFinish();
  return SERVER_STT && window.MediaRecorder ? dictateServerStart() : dictateBrowserStart();
}

// ---------------------------------------------------------------- wiring
document.addEventListener("click", e => {
  const ch = e.target.closest("[data-doc][data-page]");
  if (ch) { e.preventDefault(); openCitation(ch.dataset.doc, +ch.dataset.page, ch.dataset.q || ""); }
});
document.addEventListener("keydown", e => { if (e.key === "Escape") { if (!$("#viewer").classList.contains("hidden")) closeViewer(); else if ($("#layer").innerHTML) closeSheet(); else if (DICT.on) dictateCancel(); else if (VOICE.on) voiceClose(); } });
$("#mClose").onclick = closeViewer;
$("#btnSettings").onclick = settingsSheet;
$("#btnMenu").onclick = vehicleMenu;
$("#vehBtn").onclick = pickVehicleSheet;
$("#send").onclick = () => send();
$("#dictate").onclick = dictateToggle;
$("#dictCancel").onclick = dictateCancel; $("#dictDone").onclick = dictFinish;
$("#txt").addEventListener("input", autosize);
$("#txt").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
$("#photo").onchange = onMediaFile; $("#video").onchange = onMediaFile; $("#gallery").onchange = onMediaFile;
$("#attach").onclick = () => VID ? mediaSheet() : $("#photo").click();
document.querySelectorAll("label[role=button]").forEach(l => l.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); l.querySelector("input")?.click(); } }));
$("#mic").onclick = voiceOpen;
$("#vClose").onclick = voiceClose; $("#vExit").onclick = voiceClose; $("#vOrb").onclick = orbTap;
window.addEventListener("resize", layout);

const ROLE_HOME = {manager: "/manager", engineer: "/engineer"};
$("#l_go").onclick = async () => {
  const consent = $("#l_consent").checked, lang = $("#l_lang").value, country = $("#l_country").value || "RU";
  if (!consent) return toast(t("consent_needed"));
  const code = $("#l_code").value.trim(), pin = $("#l_pin").value.trim();
  try {
    if (!code || !pin) return toast(t("need_pin"));
    ME = (await api("/api/login/demo", {json: {account: code, pin, lang, consent, country}})).user;
  } catch (e) { return toast(String(e.message || e).includes("PIN") ? t("wrong_pin") : String(e.message || e).includes("deactivated") ? t("deactivated") : friendlyError(e)); }
  if (ROLE_HOME[ME.role]) location.href = ROLE_HOME[ME.role]; else start();
};
async function demoAccounts() {
  const box = $("#l_demo"); if (!box) return;
  const pre = new URLSearchParams(location.search).get("login"); if (pre) $("#l_code").value = pre;
  try {
    const r = await api("/api/demo/accounts");
    box.innerHTML = r.accounts.map(a => `<button type="button" class="acc" data-k="${esc(a.key)}" data-p="${esc(a.pin)}"><b>${esc(a.name)}</b><span>${esc(TT("title_" + a.key, a.title))}</span><span class="pin">${t("demo_pin")} ${esc(a.pin)}</span></button>`).join("");
    box.querySelectorAll(".acc").forEach(b => b.onclick = () => { $("#l_code").value = b.dataset.k; $("#l_pin").value = b.dataset.p; box.querySelectorAll(".acc").forEach(x => x.classList.toggle("on", x === b)); });
  } catch (_) { box.closest(".field").remove(); }
}
async function start() {
  if (ROLE_HOME[ME.role] && !new URLSearchParams(location.search).get("mechanic")) { location.href = ROLE_HOME[ME.role]; return; }
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  applyI18n();
  await loadVehicles(); autosize(); loadAssignments();
  if (!ME.onboarded) onboarding();
  ["#chat", "#quick", "#vehBtn", "#vehInfo"].forEach(sel => trQueue($(sel)));
}
(async () => {
  hydrateIcons();
  I18N = await (await fetch("/static/i18n.json")).json();
  try { const ss = await api("/api/session"); SERVER_TTS = ss.server_tts !== false; SERVER_TTS_LANGS = ss.server_tts_langs || []; SERVER_STT = WebSTT.server = ss.server_stt !== false; ME = ss.user; if (!ME) throw new Error("login"); start(); }
  catch (e) { langOptions($("#l_lang"), (navigator.language || "ru").slice(0, 2)); $("#l_lang").onchange = () => { COUNTRIES = null; $("#l_country").innerHTML = ""; applyI18n(); demoAccounts(); }; $("#l_country").onchange = loginCountry; applyI18n(); demoAccounts(); $("#login").classList.remove("hidden"); }
})();
