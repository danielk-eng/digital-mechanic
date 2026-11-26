"use strict";
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
let I18N = {}, ME = null, FLEET = null, CUR = "fleet";
const t = k => (I18N[(ME && ME.lang) || "ru"] || I18N.ru)[k] || I18N.ru[k] || k;
async function api(p, o = {}) {
  if (o.json) { o.method = "POST"; o.headers = {"Content-Type": "application/json"}; o.body = JSON.stringify(o.json); }
  const r = await fetch(p, {credentials: "same-origin", ...o});
  if (r.status === 401 || r.status === 403) { location.href = "/"; throw new Error("auth"); }
  if (!r.ok) throw new Error(await r.text()); return r.json();
}
const fmt = ts => new Date(ts * 1000).toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
const vSt = v => v.in_repair ? "repair" : v.status;
const pill = v => `<span class="pill ${vSt(v)}">${esc(v.in_repair ? t("in_repair") : t(v.status))}</span>`;
const URG = {high: "quarantine", medium: "attention", low: "ready"};

// ---- Filters for the chief: one set for every vehicle list (fleet, tickets, repairs, handoffs), kept in the URL (?status=…&make=…)
const FKEYS = ["q", "status", "make", "model", "mech", "urg", "code", "from", "to", "ops"];
const OPF = {op_in_repair: v => v.ops === "in_repair", op_awaiting: v => v.awaiting, op_on_line: v => v.ops === "on_line", op_idle: v => v.ops === "idle"};
let F = {};
function readF() { const p = new URLSearchParams(location.search); F = {}; FKEYS.forEach(k => { const v = p.get(k); if (v) F[k] = v; }); }
function writeF() {
  const p = new URLSearchParams(location.search);
  FKEYS.forEach(k => F[k] ? p.set(k, F[k]) : p.delete(k));
  if (CUR && CUR !== "fleet") p.set("tab", CUR); else p.delete("tab");
  const q = p.toString(); history.replaceState(null, "", location.pathname + (q ? "?" + q : ""));
}
const nActive = () => FKEYS.filter(k => F[k]).length;
// plates are stored in Latin letters; people type Cyrillic look-alikes, spaces and dashes
const LAT = {"А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X"};
const plate = s => String(s || "").toUpperCase().replace(/[\s\-]/g, "").replace(/[АВЕКМНОРСТУХ]/g, c => LAT[c]);
const vScan = v => ((v.last && v.last.checked_at) || "").slice(0, 10);
const vCode = v => (v.last && v.last.status === "anomaly" && v.last.anomaly_type) || "";
function vMatch(v) {
  if (!v) return !nActive();
  if (F.ops && OPF[F.ops] && !OPF[F.ops](v)) return false;
  if (F.status && (F.status === "in_repair" ? !v.in_repair : v.status !== F.status)) return false;
  if (F.make && v.make !== F.make) return false;
  if (F.model && v.model !== F.model) return false;
  if (F.mech && (F.mech === "-" ? (v.mechanics || []).length : !(v.mechanics || []).includes(F.mech))) return false;
  if (F.urg && (F.urg === "-" ? v.urgency : v.urgency !== F.urg)) return false;
  if (F.code && (F.code === "-" ? vCode(v) : vCode(v) !== F.code)) return false;
  if (F.from && !(vScan(v) && vScan(v) >= F.from)) return false;
  if (F.to && !(vScan(v) && vScan(v) <= F.to)) return false;
  if (F.q && !(plate(v.vehicle_id).includes(plate(F.q)) || plate(v.garage_no).includes(plate(F.q)))) return false;
  return true;
}
const vById = id => FLEET && FLEET.vehicles.find(v => v.vehicle_id === id);
const FTABS = ["fleet", "tickets", "repairs", "handoffs"];
function renderFbar() {
  const box = $("#fbar"); if (!box || !FLEET) return;
  box.classList.toggle("hidden", !FTABS.includes(CUR));
  const vs = FLEET.vehicles, uniq = a => [...new Set(a.filter(Boolean))].sort((x, y) => String(x).localeCompare(String(y), "ru"));
  const opt = (k, list, all, extra = "") => `<select class="input" id="f_${k}" aria-label="${esc(t("f_" + k))}"><option value="">${esc(all)}</option>${extra}` +
    list.map(([v, l]) => `<option value="${esc(v)}" ${F[k] === v ? "selected" : ""}>${esc(l)}</option>`).join("") + `</select>`;
  const makes = uniq(vs.map(v => v.make)), models = uniq(vs.filter(v => !F.make || v.make === F.make).map(v => v.model));
  const mechs = uniq(vs.flatMap(v => v.mechanics || [])), codes = uniq(vs.map(vCode));
  const shown = vs.filter(vMatch).length, n = nActive();
  const wasOpen = box.querySelector("details") ? box.querySelector("details").open : (n > 0 || matchMedia("(min-width:721px)").matches);
  box.innerHTML = `<details class="fbar" ${wasOpen ? "open" : ""}><summary>${icon("search", 16)}<b>${t("filters")}</b>${n ? `<span class="fcount">${n}</span>` : ""}<span class="small muted" id="f_found">${t("found")} ${shown} ${t("of")} ${vs.length}</span></summary>` +
    `<div class="fgrid"><input class="input" type="search" id="f_q" inputmode="search" autocomplete="off" aria-label="${esc(t("f_q"))}" placeholder="${esc(t("f_q"))}" value="${esc(F.q || "")}">` +
    opt("status", [["quarantine", t("quarantine")], ["attention", t("attention")], ["ready", t("ready")], ["in_repair", t("in_repair")]], t("f_status")) +
    opt("make", makes.map(m => [m, m]), t("f_make")) + opt("model", models.map(m => [m, m]), t("f_model")) +
    opt("mech", mechs.map(m => [m, m]), t("f_mech"), `<option value="-" ${F.mech === "-" ? "selected" : ""}>${esc(t("f_unassigned"))}</option>`) +
    opt("urg", [["high", t("urg_high")], ["medium", t("urg_medium")], ["low", t("urg_low")]], t("f_urg"), `<option value="-" ${F.urg === "-" ? "selected" : ""}>${esc(t("f_no_urg"))}</option>`) +
    opt("code", codes.map(c => [c, c]), t("f_code"), `<option value="-" ${F.code === "-" ? "selected" : ""}>${esc(t("f_no_code"))}</option>`) +
    `<label class="fdate"><span class="small muted">${t("f_from")}</span><input class="input" type="date" id="f_from" value="${esc(F.from || "")}"></label>` +
    `<label class="fdate"><span class="small muted">${t("f_to")}</span><input class="input" type="date" id="f_to" value="${esc(F.to || "")}"></label>` +
    `<button class="btn ghost sm" id="f_reset" ${n ? "" : "disabled"}>${t("f_reset")}</button></div></details>`;
  let tm = null;
  box.querySelectorAll("select, input[type=date]").forEach(el => el.onchange = () => {
    const k = el.id.slice(2); F[k] = el.value || undefined; if (k === "make") delete F.model; applyF(); });
  const q = box.querySelector("#f_q");
  q.oninput = () => { clearTimeout(tm); tm = setTimeout(() => { F.q = q.value.trim() || undefined; applyF(true); }, 200); };
  box.querySelector("#f_reset").onclick = () => { F = {}; applyF(); };
}
function applyF(keepFocus) {
  Object.keys(F).forEach(k => { if (!F[k]) delete F[k]; });
  writeF();
  if (keepFocus) { const n = nActive(), s = FLEET.vehicles.filter(vMatch).length; const f = $("#f_found"); if (f) f.textContent = `${t("found")} ${s} ${t("of")} ${FLEET.vehicles.length}`;
    const c = $("#fbar .fcount"); if (c) c.textContent = n; else if (n) $("#fbar summary b").insertAdjacentHTML("afterend", `<span class="fcount">${n}</span>`); }
  else renderFbar();
  $$(".kpi").forEach(el => el.classList.toggle("on", el.dataset.k === F.ops));
  refresh();
}
function refresh() { ({fleet: renderFleet, tickets: loadTickets, repairs: loadRepairs, handoffs: loadHandoffs}[CUR] || (() => {}))(); }

function spark(scores) {
  if (!scores || scores.length < 2) return "";
  const W = 96, H = 28, n = scores.length;
  return `<svg width="${W}" height="${H}" aria-hidden="true"><polyline fill="none" stroke="var(--text-3)" stroke-width="1.5" points="${scores.map((s, i) => `${4 + i * (W - 8) / (n - 1)},${H - 4 - s * (H - 8)}`).join(" ")}"/>` +
    `<circle cx="${W - 4}" cy="${H - 4 - scores[n - 1] * (H - 8)}" r="3" fill="${scores[n - 1] >= 0.8 ? "var(--danger)" : scores[n - 1] >= 0.5 ? "var(--warn)" : "var(--ok)"}"/></svg>`;
}

async function loadFleet() {
  FLEET = await api("/api/fleet");
  const o = FLEET.ops || {}, c = FLEET.counts;
  const idleMax = Math.max(0, ...FLEET.vehicles.filter(v => v.ops === "idle").map(v => v.idle_days || 0));
  $("#kpis").innerHTML = [["op_in_repair", o.in_repair, ""], ["op_awaiting", o.awaiting, ""], ["op_on_line", o.on_line, ""], ["op_idle", o.idle, idleMax ? `${t("idle_max")} ${idleMax} ${t("days")}` : ""]]
    .map(([k, n, s]) => `<button class="kpi ${F.ops === k ? "on" : ""}" data-k="${k}"><div class="l">${t(k)}</div><div class="n">${n ?? 0}</div>${s ? `<div class="s">${s}</div>` : `<div class="s">&nbsp;</div>`}</button>`).join("");
  $$(".kpi").forEach(el => el.onclick = () => { F.ops = F.ops === el.dataset.k ? undefined : el.dataset.k; if (CUR !== "fleet") tab("fleet"); applyF(); });
  window.__counts = c;
  if (!document.activeElement || !document.activeElement.closest("#fbar")) renderFbar();  // do not steal the cursor from the search field
  if (!$("#tab-fleet").classList.contains("hidden")) renderFleet();
}
function renderFleet() {
  const c = FLEET.counts;
  const rows = FLEET.vehicles.filter(vMatch);
  const fchip = (k, n) => `<button class="fchip ${F.status === k ? "on" : ""}" data-s="${k}">${t(k)} <span class="c">${n}</span></button>`;
  $("#tab-fleet").innerHTML = `<div class="filters"><button class="fchip ${!F.status ? "on" : ""}" data-s="">${t("all")} <span class="c">${c.total}</span></button>${fchip("quarantine", c.quarantine)}${fchip("attention", c.attention)}${fchip("ready", c.ready)}</div>` +
    `<div class="panel"><table class="tbl"><thead><tr><th>${t("vehicle")}</th><th class="hide-m">${t("model_engine")}</th><th>${t("status")}</th><th class="hide-m">${t("last_check")}</th><th class="hide-m">${t("trend")}</th><th>${t("priority")}</th></tr></thead><tbody>` +
    (rows.length ? rows.map(v => { const l = v.last || {};
      const lastTxt = l.status === "anomaly" ? `${esc(l.anomaly_type)} ${esc(l.anomaly_title)}` : l.status === "check_failed" ? t("check_failed_short") : t("status_normal");
      const extra = [v.awaiting ? t("op_awaiting") : "", v.idle_days != null ? `${t("op_idle")} ${v.idle_days} ${t("days")}` : ""].filter(Boolean).join(", ");
      return `<tr class="click" data-v="${esc(v.vehicle_id)}"><td><b class="mono">${esc(v.vehicle_id)}</b><div class="sub">${esc(v.garage_no)}</div></td>` +
        `<td class="hide-m">${esc(v.model)}<div class="sub">${esc(v.engine_model)}${(v.mechanics || []).length ? ", " + esc(v.mechanics.join(", ")) : ""}</div></td><td>${pill(v)}${extra ? `<div class="sub" style="margin-top:4px">${extra}</div>` : ""}</td>` +
        `<td class="hide-m">${lastTxt}<div class="sub">${esc((l.checked_at || "").slice(0, 10))}${l.anomaly_score != null ? `, ${l.anomaly_score}` : ""}</div></td><td class="hide-m">${spark(v.scores)}</td>` +
        `<td>${v.urgency ? `<span class="pill ${URG[v.urgency]}">${t("urg_" + v.urgency)}</span>` : ""}</td></tr>`; }).join("")
      : `<tr><td colspan="6" class="muted">${nActive() ? t("f_nothing") : t("no_vehicles")}</td></tr>`) + `</tbody></table></div>`;
  $$("#tab-fleet [data-s]").forEach(b => b.onclick = () => { F.status = b.dataset.s || undefined; applyF(); });
  $$("#tab-fleet tr[data-v]").forEach(tr => tr.onclick = () => openVehicle(tr.dataset.v));
}

function side(title, html) {
  $("#layer").innerHTML = `<div class="scrim" id="scrim"></div><aside class="side" role="dialog" aria-modal="true"><div class="vhd"><b>${esc(title)}</b><button class="icon-btn" id="sClose" aria-label="close">${icon("x")}</button></div><div class="vbd">${html}</div></aside>`;
  $("#scrim").onclick = closeSide; $("#sClose").onclick = closeSide;
}
// same page text rendering as the mechanic viewer: joined hyphenation, figure captions as captions
const TABLE_HEAD = /^(?:Вероятн\S* |Возможн\S* )?(?:Причин|Неисправн)[^\n]{0,40} — (?:Способ|Метод)/m;
function pageHtml(x) {
  if (TABLE_HEAD.test(x || "")) return esc(x).split("\n").map(l => { const k = l.indexOf(" — "); return k > 0 ? `<span class="trow"><span class="tc">${l.slice(0, k)}</span><span class="tr">${l.slice(k + 3)}</span></span>` : `<span class="tsub">${l}</span>`; }).join("");
  const r = String(x || "").replace(/([а-яё])-\n(?=[а-яё])/g, "$1").replace(/([^\n.:;!?])\n(?=[a-zа-яё(«])/g, "$1 ");
  return esc(r).replace(/(^|\n)\n((?:Рис\.|Рисунок)\s*\d[\s\S]*?)(?=\n\n|$)/g, '$1<span class="figcap">$2</span>');
}
function closeSide() { $("#layer").innerHTML = ""; }
async function openVehicle(vid, brief) {
  side(vid, `<div class="skeleton"><div class="line"></div><div class="line"></div><div class="line"></div></div>`);
  const h = await api(`/api/fleet/${encodeURIComponent(vid)}`);
  const v = h.vehicle, row = FLEET.vehicles.find(r => r.vehicle_id === vid) || {};
  let b = `<div class="top" style="display:flex;justify-content:space-between;gap:12px"><div><div style="font-weight:600">${esc(v.model && v.model.startsWith(v.make) ? v.model : v.make + " " + (v.model || ""))}</div><div class="small muted">${esc(v.engine_model)}, ${esc(v.garage_no)}</div></div>${pill(row)}</div>`;
  if (brief) {
    b += `<div class="brief"><p class="small">${t("scanned_by")}: <b>${esc(brief.scanned_by || "—")}</b>, ${esc((brief.checked_at || "").slice(0, 16).replace("T", " "))}. ${t("responsible")}: <b>${esc(brief.owner || "—")}</b></p>` +
      (brief.insights || []).map(i => `<div class="insight ${i.kind}">${icon(i.kind === "repeat" ? "rotate-ccw" : "shuffle", 16)}<span>${i.kind === "repeat" ? `${t("ins_repeat")} ${i.days} ${t("days_ago")}: ${esc(i.ticket_id)}, ${esc(i.mechanic || "")}` : `${t("ins_similar")} ${esc(i.vehicle_id)}: ${esc(i.ticket_id)}, ${esc(i.mechanic || "")}${i.result === "normal" ? ", " + t("recheck_ok") : ""}`}</span></div>`).join("") + `</div>`;
  }
  if (row.urgency_basis && row.urgency_basis.length) b += `<p class="small muted">${t("priority")} ${t("urg_" + row.urgency)}: ${row.urgency_basis.map(esc).join("; ")}</p>`;
  try { const tl = await api(`/api/history/${encodeURIComponent(vid)}`); b += arrivalCard(tl, t) + `<div class="section-label">${t("history")}</div>${renderTimeline(tl)}`; } catch (_) {}
  b += `<div class="section-label">${t("tickets")}</div>` + (h.tickets.length ? `<div class="list">` + h.tickets.map(x => `<div class="item" style="cursor:default"><span class="t"><b class="mono">${esc(x.ticket_id)}</b><span>${esc(x.at)}. ${esc(x.conclusion)}</span></span><span class="pill ${URG[x.urgency] || ""}">${t("urg_" + x.urgency)}</span></div>`).join("") + `</div>` : `<p class="muted small">${t("none")}</p>`);
  if (h.repair) b += `<div class="section-label">${esc(h.repair.title)}</div><ol class="ops">${h.repair.steps.map((s, i) => `<li class="${s.done ? "done" : i === h.repair.current ? "cur" : ""}"><span class="n">${s.done ? icon("check", 14) : i + 1}</span><span>${esc(s.text)}</span></li>`).join("")}</ol>`;
  const body = $("#layer .vbd"); if (body) body.innerHTML = b;
}

function pageLink(x) {
  return `<button class="cite" data-doc="${esc(x.doc_id)}" data-page="${x.page}" title="${esc(x.document)}">${t("page_short")} ${x.page}</button>`;
}
// In-app page viewer (same as the mechanic's): page text from the manual index + page image; PDF only as a small secondary link.
async function pageViewer(doc, page) {
  let v = $("#pv");
  if (!v) { v = document.createElement("div"); v.id = "pv"; v.className = "viewer"; document.body.appendChild(v); }
  const s = await api(`/api/section/${encodeURIComponent(doc)}/${page}`).catch(() => null);
  const pages = s ? [...new Set([...s.pages.map(p => p.page), page])].sort((a, b) => a - b) : [page];
  const show = async n => {
    const p = await api(`/api/page/${encodeURIComponent(doc)}/${n}`).catch(() => ({text: ""}));
    const i = pages.indexOf(n), prev = pages[i - 1], next = pages[i + 1];
    v.innerHTML = `<div class="vhd"><button class="icon-btn" id="pvClose">${icon("arrow-left")}</button><div class="t"><b>${t("page")} ${n}</b><span>${esc(p.full_title || "")}</span></div></div>` +
      `<div class="vbd"><div class="wrap"><div class="pages">${pages.map(k => `<button data-pg="${k}" class="${k === n ? "on" : ""}">${t("page_short")} ${k}</button>`).join("")}</div>` +
      `<section class="pageview"><div class="pagetext">${pageHtml(p.text)}</div>${p.has_image ? `<img class="pageimg" src="/page/${encodeURIComponent(doc)}/${n}.png" alt="">` : ""}</section>` +
      `<div class="pnav"><button class="btn ghost sm" data-pg="${prev ?? ""}" ${prev == null ? "disabled" : ""}>${icon("chevron-left", 18)}${t("page_short")} ${prev ?? "—"}</button>` +
      `<button class="btn ghost sm" data-pg="${next ?? ""}" ${next == null ? "disabled" : ""}>${t("page_short")} ${next ?? "—"}${icon("chevron-right", 18)}</button></div>` +
      (s && s.pdf ? `<p class="pdflink"><a target="_blank" rel="noopener" href="/manuals/${encodeURIComponent(doc)}.pdf#page=${n}">${icon("external-link", 14)}${t("open_pdf")}</a></p>` : "") + `</div></div>`;
    v.querySelector("#pvClose").onclick = () => v.remove();
    v.querySelectorAll("[data-pg]").forEach(b => { if (b.dataset.pg) b.onclick = () => show(+b.dataset.pg); });
  };
  await show(+page);
}
function mdl(s) { return esc(s || "").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>"); }
function attHtml(a) {
  if (!a) return `<p class="muted small">${t("no_attachments")}</p>`;
  return `<div>${mdl(a.summary)}</div>` + (a.answer ? `<details class="more"><summary>${icon("chevron-right", 16)}${t("agent_answer")}</summary><div class="small">${mdl(a.answer)}</div></details>` : "") +
    (a.pages && a.pages.length ? `<div style="margin-top:8px">${a.pages.map(pageLink).join(" ")}</div>` : "") +
    (a.inspection && a.inspection.length ? `<p class="small">${t("inspection")}: ${a.inspection.map(it => `${esc(it.component)}: ${esc(it.finding)}`).join("; ")}</p>` : "") +
    (a.photos && a.photos.length ? `<div style="display:flex;gap:8px;margin-top:8px">${a.photos.map(n => `<a target="_blank" href="/api/photo/${encodeURIComponent(n)}"><img src="/api/photo/${encodeURIComponent(n)}" alt="" style="height:72px;border-radius:8px"></a>`).join("")}</div>` : "");
}
const TSTATUS = {created: "t_created", approved: "t_approved", returned: "t_returned"};
async function loadTickets() {
  const r = await api("/api/tickets"), rows = r.tickets.filter(x => vMatch(vById(x.vehicle_id)));
  $("#tab-tickets").innerHTML = `<div class="panel">` + (rows.length ? `<table class="tbl"><thead><tr><th>№</th><th>${t("vehicle")}</th><th>${t("priority")}</th><th class="hide-m">${t("diagnosis")}</th><th class="hide-m">${t("created")}</th><th>${t("status")}</th></tr></thead><tbody>` + rows.map((x, i) =>
    `<tr class="click" data-i="${i}"><td><b class="mono">${esc(x.ticket_id)}</b></td><td class="mono">${esc(x.vehicle_id)}</td><td><span class="pill ${URG[x.urgency] || ""}">${t("urg_" + x.urgency)}</span></td><td class="hide-m">${esc(x.anomaly_type)}. ${esc(x.conclusion)}</td><td class="hide-m sub">${esc(x.at)}</td><td>${esc(t(TSTATUS[x.status] || x.status || ""))}</td></tr>` +
    `<tr class="hidden" id="tk${i}"><td colspan="6" style="background:var(--surface-2)">${attHtml(x.attachments)}<div class="card-actions" style="justify-content:flex-start">${reviewHtml(x)}<button class="btn ghost sm" data-veh="${esc(x.vehicle_id)}">${t("vehicle_card")}</button></div></td></tr>`).join("") + `</tbody></table>`
    : `<div class="pad muted">${nActive() ? t("f_nothing") : t("no_tickets")}</div>`) + `</div>`;
  $$("#tab-tickets tr[data-i]").forEach(tr => tr.onclick = () => $("#tk" + tr.dataset.i).classList.toggle("hidden"));
  $$("#tab-tickets [data-veh]").forEach(b => b.onclick = ev => { ev.stopPropagation(); openVehicle(b.dataset.veh); });
  $$("#tab-tickets button[data-rv]").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    const comment = b.dataset.d === "returned" ? prompt(t("return_what")) : "";
    if (comment === null) return;
    await api(`/api/ticket/${encodeURIComponent(b.dataset.rv)}/review`, {json: {decision: b.dataset.d, comment}}); loadTickets(); loadFleet();
  });
}
function reviewHtml(x) {
  const rv = x.review ? `<span class="small muted">${x.review.decision === "approved" ? t("t_approved") : t("t_returned")}${x.review.by ? ", " + esc(x.review.by) : ""}${x.review.comment ? ": " + esc(x.review.comment) : ""}</span>` : "";
  return rv + (x.status === "created" || x.status === "returned" ? `<button class="btn primary sm" data-rv="${esc(x.ticket_id)}" data-d="approved">${t("approve")}</button><button class="btn ghost sm" data-rv="${esc(x.ticket_id)}" data-d="returned">${t("return")}</button>` : "");
}
async function loadHandoffs() {
  const r = await api("/api/handoffs");
  const st = {open: "h_open", taken: "h_taken", closed: "h_closed"}, stc = {open: "attention", taken: "repair", closed: "ready"};
  r.handoffs = r.handoffs.filter(h => vMatch(vById(h.vehicle_id)));
  r.handoffs.forEach(h => { h.payload = Object.assign({pages: [], photos: [], dialog: [], items: []}, h.payload); });
  $("#tab-handoffs").innerHTML = r.handoffs.length ? r.handoffs.map(h => `<div class="panel"><div class="pad">` +
    `<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><b class="mono">${esc(h.vehicle_id)}</b><span class="small muted">${h.kind === "inspection" ? t("inspection") + ", " : ""}${new Date(h.ts * 1000).toLocaleString("ru-RU")}, ${esc(h.user_name)}</span><span class="pill ${stc[h.status]}" style="margin-left:auto">${t(st[h.status] || h.status)}</span></div>` +
    (h.note ? `<p><b>${esc(h.note)}</b></p>` : "") + `<div class="small" style="margin-top:8px">${mdl(h.summary)}</div>` +
    (h.payload.items.length ? `<ul class="small">${h.payload.items.map(it => `<li><b>${esc(it.component)}</b>: ${esc(it.finding)} <span class="muted">(${esc(it.severity)})</span>${it.page ? " " + pageLink(it) : ""}</li>`).join("")}</ul>` : "") +
    (h.payload.pages.length ? `<div style="margin-top:8px">${h.payload.pages.map(pageLink).join(" ")}</div>` : "") +
    (h.payload.dialog.length ? `<details class="more"><summary>${icon("chevron-right", 16)}${t("dialog")} (${h.payload.dialog.length})</summary>${h.payload.dialog.map(d => `<div class="msg ${d.role === "user" ? "user" : "bot"}" style="margin:8px 0"><div class="b small">${mdl(d.text)}</div></div>`).join("")}</details>` : "") +
    `<div class="card-actions" style="justify-content:flex-start">${h.status === "open" ? `<button class="btn primary sm" data-h="${h.id}" data-s="taken">${t("take")}</button>` : ""}${h.status !== "closed" ? `<button class="btn ghost sm" data-h="${h.id}" data-s="closed">${t("close_case")}</button>` : ""}<button class="btn ghost sm" data-veh="${esc(h.vehicle_id)}">${t("vehicle_card")}</button></div></div></div>`).join("")
    : `<div class="panel"><div class="pad muted">${nActive() ? t("f_nothing") : t("no_handoffs")}</div></div>`;
  $$("#tab-handoffs button[data-h]").forEach(b => b.onclick = async () => {
    const comment = b.dataset.s === "closed" ? (prompt(t("comment_opt")) || "") : "";
    await api(`/api/handoff/${b.dataset.h}`, {json: {status: b.dataset.s, comment}}); loadHandoffs(); loadFleet(); });
  $$("#tab-handoffs [data-veh]").forEach(b => b.onclick = () => openVehicle(b.dataset.veh));
}
async function loadRepairs() {
  const vs = FLEET.vehicles.filter(v => v.in_repair && vMatch(v));
  if (!vs.length) { $("#tab-repairs").innerHTML = `<div class="panel"><div class="pad muted">${nActive() ? t("f_nothing") : t("no_repairs")}</div></div>`; return; }
  let h = `<div class="panel"><table class="tbl"><thead><tr><th>${t("vehicle")}</th><th>${t("works")}</th><th>${t("progress")}</th><th class="hide-m">${t("in_work")}</th></tr></thead><tbody>`;
  for (const v of vs) { const d = await api(`/api/fleet/${encodeURIComponent(v.vehicle_id)}`); const r = d.repair; if (!r) continue;
    const done = r.steps.filter(s => s.done).length, mins = Math.round((Date.now() / 1000 - r.started) / 60);
    h += `<tr class="click" data-v="${esc(v.vehicle_id)}"><td class="mono"><b>${esc(v.vehicle_id)}</b></td><td>${esc(r.title)}</td><td class="num">${done}/${r.steps.length}<div style="background:var(--surface-3);border-radius:3px;height:6px;margin-top:6px;width:120px"><div style="background:var(--accent);height:6px;border-radius:3px;width:${100 * done / r.steps.length}%"></div></div></td><td class="hide-m num">${mins} ${t("min")}</td></tr>`; }
  $("#tab-repairs").innerHTML = h + `</tbody></table></div>`;
  $$("#tab-repairs tr[data-v]").forEach(tr => tr.onclick = () => openVehicle(tr.dataset.v));
}
async function loadAudit() {
  const r = await api("/api/audit?limit=300");
  $("#tab-audit").innerHTML = `<div class="filters"><a class="btn ghost sm" href="/api/audit.csv">CSV</a><span class="small muted" style="align-self:center">${t("audit_note")}</span></div><div class="panel" style="overflow:auto"><table class="tbl"><thead><tr><th>${t("time")}</th><th>${t("who")}</th><th>${t("vehicle")}</th><th>${t("action")}</th><th>${t("request")}</th><th class="hide-m">${t("answer")}</th><th class="hide-m">${t("sources_h")}</th><th>${t("ms")}</th></tr></thead><tbody>` +
    r.rows.map(a => `<tr><td class="sub">${fmt(a.ts)}</td><td>${esc(a.user_name || a.user_id)}<div class="sub">${esc(a.role)}</div></td><td class="mono">${esc(a.vehicle_id)}</td><td>${esc(a.action)}${a.ok ? "" : ` <span class="err">!</span>`}</td><td class="small">${esc((a.input || "").slice(0, 120))}</td><td class="hide-m small">${esc((a.output || "").slice(0, 160))}</td><td class="hide-m sub">${Array.isArray(a.sources) ? a.sources.map(esc).join("; ") : ""}</td><td class="num">${a.latency_ms ?? ""}</td></tr>`).join("") + `</tbody></table></div>`;
}
async function loadEcon() {
  const u = await api("/api/usage"), per = u.calls ? (u.prompt_tokens + u.completion_tokens) / u.calls : 0;
  $("#tab-econ").innerHTML = `<div class="kpis" style="margin-top:16px">` + [[t("llm_calls"), u.calls], [t("tokens_io"), `${u.prompt_tokens} / ${u.completion_tokens}`], [t("avg_call"), `${u.avg_seconds} ${t("sec")}`], [t("tokens_per_call"), Math.round(per)]]
    .map(([l, n]) => `<div class="kpi" style="cursor:default"><div class="l">${l}</div><div class="n" style="font-size:1.5rem">${n}</div></div>`).join("") + `</div><p class="small muted">${t("econ_note")}</p>`;
}
function tab(name) {
  CUR = name; writeF(); renderFbar();
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $$("section[id^=tab-]").forEach(s => s.classList.toggle("hidden", s.id !== "tab-" + name));
  ({fleet: renderFleet, tickets: loadTickets, repairs: loadRepairs, audit: loadAudit, econ: loadEcon, handoffs: loadHandoffs, users: loadUsers}[name] || (() => {}))();
}
$$("#tabs button").forEach(b => b.onclick = () => tab(b.dataset.tab));
// Agent tab: text or voice. The briefing answer opens the vehicle card, cites pages and is read aloud.
function mdc(text, sources) {
  const by = Object.fromEntries((sources || []).map(s => [s.label, s]));
  return mdl(text).replace(/\[(S\d+)\]/g, (m, l) => by[l] ? pageLink(by[l]) : "");
}
function speak(text) {
  if (!window.speechSynthesis || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.lang = "ru-RU"; u.rate = 1.05;
  const v = speechSynthesis.getVoices().find(x => x.lang.startsWith("ru")); if (v) u.voice = v;
  speechSynthesis.speak(u);
}
async function ask(textIn) {
  const txt = (textIn || $("#mtxt").value).trim(); if (!txt) return; $("#mtxt").value = "";
  const add = (h, me) => { const d = document.createElement("div"); d.className = "msg " + (me ? "user" : "bot"); d.innerHTML = `<div class="b">${h}</div>`; $("#mchat").appendChild(d); d.scrollIntoView({block: "end"}); return d; };
  add(esc(txt), true); const th = add(`<div class="skeleton"><div class="line"></div><div class="line"></div></div>`); th.dataset.busy = 1;
  try {
    const r = await api("/api/chat", {json: {text: txt}});
    th.querySelector(".b").innerHTML = mdc(r.text, r.sources) +
      ((r.actions || []).length ? `<div class="answer-actions">${r.actions.map((a, i) => `<button class="btn sm ${i ? "ghost" : "primary"}" data-say="${esc(a.text || a.label)}">${esc(a.label)}</button>`).join("")}</div>` : "");
    th.querySelectorAll("[data-say]").forEach(b => b.onclick = () => ask(b.dataset.say));
    delete th.dataset.busy;
    const brief = (r.cards || []).find(c => c.kind === "vehicle_brief");
    if (brief) { await loadFleet(); openVehicle(brief.vehicle_id, brief); }
    if ((r.cards || []).some(c => c.kind === "ticket_created")) { loadFleet(); }
    if (MVOICE) speak(r.speak || r.text.replace(/\*\*|\[S\d+\]/g, ""));
  } catch (e) { th.querySelector(".b").textContent = e.message; delete th.dataset.busy; }
}
// Voice: record -> /api/stt (server STT with the data-law routing) -> the same ask(); replies are spoken
let MVOICE = false, MREC = null;
async function micToggle() {
  const b = $("#mmic");
  if (MREC) { MREC.stop(); return; }
  let stream; try { stream = await navigator.mediaDevices.getUserMedia({audio: true}); } catch (_) { alert(t("mic_denied")); return; }
  const chunks = [], rec = new MediaRecorder(stream); MREC = rec; MVOICE = true; b.classList.add("on");
  rec.ondataavailable = e => e.data.size && chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach(x => x.stop()); MREC = null; b.classList.remove("on");
    const fd = new FormData(); fd.append("audio", new Blob(chunks, {type: rec.mimeType || "audio/webm"}), "q.webm"); fd.append("lang", (ME && ME.lang) || "ru");
    try { const r = await api("/api/stt", {method: "POST", body: fd}); if (r.text) ask(r.text); } catch (e) { alert(e.message); }
  };
  rec.start();
  setTimeout(() => { if (MREC === rec) rec.stop(); }, window.__micMs || 6000);  // short commands; tap again to stop earlier
}

// ---------------------------------------------------------------- employees (only the chief: server-side RBAC)
async function loadUsers() {
  const r = await api("/api/admin/users");
  const ROLE = {mechanic: t("role_mechanic"), manager: t("role_chief"), engineer: t("role_engineer")};
  $("#tab-users").innerHTML = `<div class="filters"><button class="btn primary sm" id="uNew">${icon("plus", 16)}<span>${t("user_new")}</span></button></div>` +
    `<div class="panel" style="overflow:auto"><table class="tbl"><thead><tr><th>${t("employee")}</th><th>${t("role")}</th><th class="hide-m">${t("vehicles_assigned")}</th><th class="hide-m">${t("style_short")}</th><th>${t("status")}</th></tr></thead><tbody>` +
    r.users.map(u => `<tr class="click" data-u="${esc(u.id)}"><td><b>${esc(u.name)}</b><div class="sub mono">${esc(u.login)}</div><div class="sub">${esc(u.title || "")}</div></td><td>${esc(ROLE[u.role] || u.role)}</td>` +
      `<td class="hide-m mono small">${u.vehicles.map(esc).join(", ")}</td><td class="hide-m small">${esc(u.style.slice(0, 90))}… <span class="sub">v${u.style_v}</span></td>` +
      `<td>${u.active ? `<span class="pill ready">${t("active")}</span>` : `<span class="pill quarantine">${t("inactive")}</span>`}${u.onboarded ? "" : `<div class="sub">${t("not_onboarded")}</div>`}</td></tr>`).join("") + `</tbody></table></div>`;
  $$("#tab-users tr[data-u]").forEach(tr => tr.onclick = () => userCard(tr.dataset.u));
  $("#uNew").onclick = () => newUser(r);
}
function newUser(r) {
  side(t("user_new"), `<div class="field"><label class="label" for="nu_name">${t("login_name")}</label><input id="nu_name" class="input"></div>` +
    `<div class="field"><label class="label" for="nu_title">${t("job_title")}</label><input id="nu_title" class="input"></div>` +
    `<div class="field"><label class="label" for="nu_email">${t("email_qr")}</label><input id="nu_email" class="input" type="email" autocomplete="off"></div>` +
    `<div class="field"><label class="label" for="nu_role">${t("role")}</label><select id="nu_role" class="input">${r.roles.map(x => `<option value="${x}">${esc({mechanic: t("role_mechanic"), manager: t("role_chief"), engineer: t("role_engineer")}[x])}</option>`).join("")}</select></div>` +
    `<div class="field"><label class="label" for="nu_exp">${t("level")}</label><select id="nu_exp" class="input">${r.levels.map(x => `<option value="${x}" ${x === "experienced" ? "selected" : ""}>${esc(t("lvl_" + x))}</option>`).join("")}</select></div>` +
    `<div class="field"><label class="label" for="nu_lang">${t("language")}</label><select id="nu_lang" class="input">${Object.keys(I18N).map(l => `<option value="${l}">${l}</option>`).join("")}</select></div>` +
    `<div class="field"><label class="label" for="nu_country">${t("country")}</label><select id="nu_country" class="input">${["RU", "UZ", "KZ", "KG", "BY", "TJ", "IN", "VN"].map(c => `<option>${c}</option>`).join("")}</select></div>` +
    `<div class="field"><span class="label">${t("vehicles_assigned")}</span><div class="vpick">${r.vehicles.map(v => `<label><input type="checkbox" value="${esc(v)}"> <span class="mono">${esc(v)}</span></label>`).join("")}</div></div>` +
    `<div class="card-actions"><button class="btn primary" id="nu_go">${t("create")}</button></div><div id="nu_out"></div>`);
  $("#nu_go").onclick = async () => {
    const body = {name: $("#nu_name").value.trim(), title: $("#nu_title").value.trim(), role: $("#nu_role").value, experience: $("#nu_exp").value, lang: $("#nu_lang").value,
                  country: $("#nu_country").value, vehicles: $$(".vpick input:checked").map(x => x.value), email: $("#nu_email").value.trim()};
    if (!body.name) return;
    try {
      const x = await api("/api/admin/users", {json: body});
      $("#nu_out").innerHTML = `<div class="panel"><div class="pad"><p>${t("user_created")}</p><p>${t("login_code")}: <b class="mono">${esc(x.login)}</b>, ${t("pin")}: <b class="mono">${esc(x.pin)}</b></p>` +
        `<p class="small muted">${t("pin_once")}</p><p class="small">${t("invite")}: <span class="mono">${esc(location.origin + x.invite)}</span></p></div></div>`;
      $("#nu_go").disabled = true; loadUsers();
    } catch (e) { $("#nu_out").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  };
}
async function userCard(id) {
  side(id, `<div class="skeleton"><div class="line"></div><div class="line"></div></div>`);
  const c = await api(`/api/admin/users/${encodeURIComponent(id)}`), u = c.user;
  const chips = xs => xs.length ? xs.map(x => `<span class="term">${esc(x)}</span>`).join(" ") : `<span class="muted">${t("none")}</span>`;
  let b = `<div style="font-weight:600">${esc(u.name)}</div><div class="small muted">${esc(u.title || "")}, ${esc(u.login)}, ${esc(u.lang)}/${esc(u.country)}</div>` +
    `<div class="section-label">${t("style_title")}</div><p class="small">${esc(c.style.text)}</p><p class="sub">v${c.style.v}, ${esc(c.style.source || "")}${c.style.memory ? "" : ", " + t("memory_off")}</p>` +
    (c.note ? `<p class="small muted">${esc(c.note)}</p>` : "") +
    `<div class="section-label">${t("interests")}</div><div class="terms">${chips(c.interests)}</div>` +
    `<div class="section-label">${t("topics")}</div><div class="terms">${chips(c.topics)}</div>` +
    `<div class="section-label">${t("vehicles_assigned")}</div><div class="terms">${chips(c.vehicles)}</div>` +
    `<div class="section-label">${t("worked_on")}</div><div class="terms">${chips(c.worked_on)}</div>` +
    `<div class="section-label">${t("history")}</div>` + (c.history.length ? `<ul class="small">${c.history.map(h => `<li><b class="mono">${esc(h.vehicle_id)}</b> ${esc(h.date || "")} ${esc(h.ticket_id || "")}: ${esc((h.done || (h.repair_steps || []).join("; ")).slice(0, 160))}</li>`).join("")}</ul>` : `<p class="muted small">${t("none")}</p>`) +
    (c.assigned.length ? `<div class="section-label">${t("assigned_to_him")}</div><ul class="small">${c.assigned.map(a => `<li class="mono">${esc(a.ticket_id)} ${esc(a.vehicle_id)} (${esc(a.status)})</li>`).join("")}</ul>` : "") +
    (c.recent_questions.length ? `<div class="section-label">${t("recent_questions")}</div><ul class="small">${c.recent_questions.map(q => `<li>${esc(q.text)} <span class="sub mono">${esc(q.vehicle_id || "")}</span></li>`).join("")}</ul>` : "") +
    (id.startsWith("demo-") ? `<p class="sub">${t("demo_fixed")}</p>` :  // demo PINs are printed on the login screen: fixed
    `<div class="card-actions" style="justify-content:flex-start"><button class="btn ghost sm" id="uc_pin">${t("new_pin")}</button>` +
    `<button class="btn ${u.active ? "danger" : "primary"} sm" id="uc_act">${u.active ? t("deactivate") : t("activate")}</button></div>`) + `<div id="uc_out"></div>` +
    `<div class="section-label" id="uc_qr_t"></div><div id="uc_qr"></div>`;
  const body = $("#layer .vbd"); if (body) body.innerHTML = b;
  if (window.QRLink && $("#uc_qr")) { QRLink.mount($("#uc_qr"), {userId: id, email: u.email, lang: ME.lang}); QRLink.t("title", ME.lang).then(x => $("#uc_qr_t").textContent = x); }
  if (!$("#uc_pin")) return;
  $("#uc_pin").onclick = async () => { const x = await api(`/api/admin/users/${encodeURIComponent(id)}`, {json: {new_pin: true}}); $("#uc_out").innerHTML = `<p>${t("pin")}: <b class="mono">${esc(x.pin)}</b> <span class="small muted">${t("pin_once")}</span></p>`; };
  $("#uc_act").onclick = async () => { await api(`/api/admin/users/${encodeURIComponent(id)}`, {json: {active: !u.active}}); loadUsers(); userCard(id); };
}
$("#msend").onclick = () => ask(); $("#mmic").onclick = micToggle; $("#mtxt").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } };
$("#myqr").onclick = async () => { side(await QRLink.t("mine", ME.lang), `<div id="my_qr"></div>`); QRLink.mount($("#my_qr"), {email: ME.email, lang: ME.lang}); };
$("#logout").onclick = async () => { await fetch("/api/logout", {method: "POST"}); location.href = "/"; };
const TH_IC = {dark: "moon", light: "sun", contrast: "contrast"};
function themeIcon() { $("#theme").innerHTML = icon(TH_IC[A11Y.theme()]); }
$("#theme").onclick = () => { const th = A11Y.THEMES; A11Y.setTheme(th[(th.indexOf(A11Y.theme()) + 1) % th.length]); themeIcon(); };
document.addEventListener("keydown", e => { if (e.key === "Escape") { if ($("#pv")) $("#pv").remove(); else closeSide(); } });
document.addEventListener("click", e => {
  const ch = e.target.closest("button.cite[data-doc]"); if (!ch) return;
  e.preventDefault(); pageViewer(ch.dataset.doc, +ch.dataset.page);
});
(async () => {
  document.querySelectorAll("[data-icon]").forEach(el => el.innerHTML = icon(el.dataset.icon));
  I18N = await (await fetch("/static/i18n.json")).json();
  ME = (await api("/api/session")).user;
  if (!ME || ME.role !== "manager") { location.href = ME && ME.role === "engineer" ? "/engineer" : "/"; return; }
  $$("[data-t]").forEach(el => el.textContent = t(el.dataset.t));
  $("#whoami").textContent = `${ME.name}, ${t("role_chief")}`;
  if (!ME.chief) { const ub = $('#tabs button[data-tab="users"]'); if (ub) ub.remove(); }  // staff: only the chief signed in by code + PIN
  readF();
  themeIcon();
  await loadFleet();
  const t0 = new URLSearchParams(location.search).get("tab");
  if (t0 && $(`#tabs button[data-tab="${t0}"]`)) tab(t0); else { renderFbar(); renderFleet(); }
  setInterval(loadFleet, 30000);
})();
