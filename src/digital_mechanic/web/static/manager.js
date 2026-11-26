"use strict";
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
let I18N = {}, ME = null, FLEET = null, FILTER = null, STATUS = null;
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
    .map(([k, n, s]) => `<button class="kpi ${FILTER === k ? "on" : ""}" data-k="${k}"><div class="l">${t(k)}</div><div class="n">${n ?? 0}</div>${s ? `<div class="s">${s}</div>` : `<div class="s">&nbsp;</div>`}</button>`).join("");
  $$(".kpi").forEach(el => el.onclick = () => { FILTER = FILTER === el.dataset.k ? null : el.dataset.k; tab("fleet"); loadFleetUI(); });
  window.__counts = c;
  if (!$("#tab-fleet").classList.contains("hidden")) renderFleet();
}
function loadFleetUI() { $$(".kpi").forEach(el => el.classList.toggle("on", el.dataset.k === FILTER)); renderFleet(); }
function renderFleet() {
  const c = FLEET.counts;
  const OPF = {op_in_repair: v => v.ops === "in_repair", op_awaiting: v => v.awaiting, op_on_line: v => v.ops === "on_line", op_idle: v => v.ops === "idle"};
  const rows = FLEET.vehicles.filter(v => (!FILTER || OPF[FILTER](v)) && (!STATUS || v.status === STATUS));
  const fchip = (k, n) => `<button class="fchip ${STATUS === k ? "on" : ""}" data-s="${k}">${t(k)} <span class="c">${n}</span></button>`;
  $("#tab-fleet").innerHTML = `<div class="filters"><button class="fchip ${!STATUS ? "on" : ""}" data-s="">${t("all")} <span class="c">${c.total}</span></button>${fchip("quarantine", c.quarantine)}${fchip("attention", c.attention)}${fchip("ready", c.ready)}</div>` +
    `<div class="panel"><table class="tbl"><thead><tr><th>${t("vehicle")}</th><th class="hide-m">${t("model_engine")}</th><th>${t("status")}</th><th class="hide-m">${t("last_check")}</th><th class="hide-m">${t("trend")}</th><th>${t("priority")}</th></tr></thead><tbody>` +
    (rows.length ? rows.map(v => { const l = v.last || {};
      const lastTxt = l.status === "anomaly" ? `${esc(l.anomaly_type)} ${esc(l.anomaly_title)}` : l.status === "check_failed" ? t("check_failed_short") : t("status_normal");
      const extra = [v.awaiting ? t("op_awaiting") : "", v.idle_days != null ? `${t("op_idle")} ${v.idle_days} ${t("days")}` : ""].filter(Boolean).join(", ");
      return `<tr class="click" data-v="${esc(v.vehicle_id)}"><td><b class="mono">${esc(v.vehicle_id)}</b><div class="sub">${esc(v.garage_no)}</div></td>` +
        `<td class="hide-m">${esc(v.model)}<div class="sub">${esc(v.engine_model)}</div></td><td>${pill(v)}${extra ? `<div class="sub" style="margin-top:4px">${extra}</div>` : ""}</td>` +
        `<td class="hide-m">${lastTxt}<div class="sub">${esc((l.checked_at || "").slice(0, 10))}${l.anomaly_score != null ? `, ${l.anomaly_score}` : ""}</div></td><td class="hide-m">${spark(v.scores)}</td>` +
        `<td>${v.urgency ? `<span class="pill ${URG[v.urgency]}">${t("urg_" + v.urgency)}</span>` : ""}</td></tr>`; }).join("")
      : `<tr><td colspan="6" class="muted">${t("no_vehicles")}</td></tr>`) + `</tbody></table></div>`;
  $$("#tab-fleet [data-s]").forEach(b => b.onclick = () => { STATUS = b.dataset.s || null; renderFleet(); });
  $$("#tab-fleet tr[data-v]").forEach(tr => tr.onclick = () => openVehicle(tr.dataset.v));
}

function side(title, html) {
  $("#layer").innerHTML = `<div class="scrim" id="scrim"></div><aside class="side" role="dialog" aria-modal="true"><div class="vhd"><b>${esc(title)}</b><button class="icon-btn" id="sClose" aria-label="close">${icon("x")}</button></div><div class="vbd">${html}</div></aside>`;
  $("#scrim").onclick = closeSide; $("#sClose").onclick = closeSide;
}
function closeSide() { $("#layer").innerHTML = ""; }
async function openVehicle(vid) {
  side(vid, `<div class="skeleton"><div class="line"></div><div class="line"></div><div class="line"></div></div>`);
  const h = await api(`/api/fleet/${encodeURIComponent(vid)}`);
  const v = h.vehicle, row = FLEET.vehicles.find(r => r.vehicle_id === vid) || {};
  let b = `<div class="top" style="display:flex;justify-content:space-between;gap:12px"><div><div style="font-weight:600">${esc(v.model && v.model.startsWith(v.make) ? v.model : v.make + " " + (v.model || ""))}</div><div class="small muted">${esc(v.engine_model)}, ${esc(v.garage_no)}</div></div>${pill(row)}</div>`;
  if (row.urgency_basis && row.urgency_basis.length) b += `<p class="small muted">${t("priority")} ${t("urg_" + row.urgency)}: ${row.urgency_basis.map(esc).join("; ")}</p>`;
  try { const tl = await api(`/api/history/${encodeURIComponent(vid)}`); b += arrivalCard(tl, t) + `<div class="section-label">${t("history")}</div>${renderTimeline(tl)}`; } catch (_) {}
  b += `<div class="section-label">${t("tickets")}</div>` + (h.tickets.length ? `<div class="list">` + h.tickets.map(x => `<div class="item" style="cursor:default"><span class="t"><b class="mono">${esc(x.ticket_id)}</b><span>${esc(x.at)}. ${esc(x.conclusion)}</span></span><span class="pill ${URG[x.urgency] || ""}">${t("urg_" + x.urgency)}</span></div>`).join("") + `</div>` : `<p class="muted small">${t("none")}</p>`);
  if (h.repair) b += `<div class="section-label">${esc(h.repair.title)}</div><ol class="ops">${h.repair.steps.map((s, i) => `<li class="${s.done ? "done" : i === h.repair.current ? "cur" : ""}"><span class="n">${s.done ? icon("check", 14) : i + 1}</span><span>${esc(s.text)}</span></li>`).join("")}</ol>`;
  const body = $("#layer .vbd"); if (body) body.innerHTML = b;
}

function pageLink(x) {
  const href = x.extra ? `/page/${encodeURIComponent(x.doc_id)}/${x.page}.png` : `/manuals/${encodeURIComponent(x.doc_id)}.pdf#page=${x.page}`;
  return `<a class="cite" target="_blank" href="${href}" title="${esc(x.document)}">${t("page_short")} ${x.page}</a>`;
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
  const r = await api("/api/tickets"), rows = r.tickets;
  $("#tab-tickets").innerHTML = `<div class="panel">` + (rows.length ? `<table class="tbl"><thead><tr><th>№</th><th>${t("vehicle")}</th><th>${t("priority")}</th><th class="hide-m">${t("diagnosis")}</th><th class="hide-m">${t("created")}</th><th>${t("status")}</th></tr></thead><tbody>` + rows.map((x, i) =>
    `<tr class="click" data-i="${i}"><td><b class="mono">${esc(x.ticket_id)}</b></td><td class="mono">${esc(x.vehicle_id)}</td><td><span class="pill ${URG[x.urgency] || ""}">${t("urg_" + x.urgency)}</span></td><td class="hide-m">${esc(x.anomaly_type)}. ${esc(x.conclusion)}</td><td class="hide-m sub">${esc(x.at)}</td><td>${esc(t(TSTATUS[x.status] || x.status || ""))}</td></tr>` +
    `<tr class="hidden" id="tk${i}"><td colspan="6" style="background:var(--surface-2)">${attHtml(x.attachments)}<div class="card-actions" style="justify-content:flex-start">${reviewHtml(x)}<button class="btn ghost sm" data-veh="${esc(x.vehicle_id)}">${t("vehicle_card")}</button></div></td></tr>`).join("") + `</tbody></table>`
    : `<div class="pad muted">${t("no_tickets")}</div>`) + `</div>`;
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
  r.handoffs.forEach(h => { h.payload = Object.assign({pages: [], photos: [], dialog: [], items: []}, h.payload); });
  $("#tab-handoffs").innerHTML = r.handoffs.length ? r.handoffs.map(h => `<div class="panel"><div class="pad">` +
    `<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><b class="mono">${esc(h.vehicle_id)}</b><span class="small muted">${h.kind === "inspection" ? t("inspection") + ", " : ""}${new Date(h.ts * 1000).toLocaleString("ru-RU")}, ${esc(h.user_name)}</span><span class="pill ${stc[h.status]}" style="margin-left:auto">${t(st[h.status] || h.status)}</span></div>` +
    (h.note ? `<p><b>${esc(h.note)}</b></p>` : "") + `<div class="small" style="margin-top:8px">${mdl(h.summary)}</div>` +
    (h.payload.items.length ? `<ul class="small">${h.payload.items.map(it => `<li><b>${esc(it.component)}</b>: ${esc(it.finding)} <span class="muted">(${esc(it.severity)})</span>${it.page ? " " + pageLink(it) : ""}</li>`).join("")}</ul>` : "") +
    (h.payload.pages.length ? `<div style="margin-top:8px">${h.payload.pages.map(pageLink).join(" ")}</div>` : "") +
    (h.payload.dialog.length ? `<details class="more"><summary>${icon("chevron-right", 16)}${t("dialog")} (${h.payload.dialog.length})</summary>${h.payload.dialog.map(d => `<div class="msg ${d.role === "user" ? "user" : "bot"}" style="margin:8px 0"><div class="b small">${mdl(d.text)}</div></div>`).join("")}</details>` : "") +
    `<div class="card-actions" style="justify-content:flex-start">${h.status === "open" ? `<button class="btn primary sm" data-h="${h.id}" data-s="taken">${t("take")}</button>` : ""}${h.status !== "closed" ? `<button class="btn ghost sm" data-h="${h.id}" data-s="closed">${t("close_case")}</button>` : ""}<button class="btn ghost sm" data-veh="${esc(h.vehicle_id)}">${t("vehicle_card")}</button></div></div></div>`).join("")
    : `<div class="panel"><div class="pad muted">${t("no_handoffs")}</div></div>`;
  $$("#tab-handoffs button[data-h]").forEach(b => b.onclick = async () => {
    const comment = b.dataset.s === "closed" ? (prompt(t("comment_opt")) || "") : "";
    await api(`/api/handoff/${b.dataset.h}`, {json: {status: b.dataset.s, comment}}); loadHandoffs(); loadFleet(); });
  $$("#tab-handoffs [data-veh]").forEach(b => b.onclick = () => openVehicle(b.dataset.veh));
}
async function loadRepairs() {
  const vs = FLEET.vehicles.filter(v => v.in_repair);
  if (!vs.length) { $("#tab-repairs").innerHTML = `<div class="panel"><div class="pad muted">${t("no_repairs")}</div></div>`; return; }
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
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $$("section[id^=tab-]").forEach(s => s.classList.toggle("hidden", s.id !== "tab-" + name));
  ({fleet: renderFleet, tickets: loadTickets, repairs: loadRepairs, audit: loadAudit, econ: loadEcon, handoffs: loadHandoffs}[name] || (() => {}))();
}
$$("#tabs button").forEach(b => b.onclick = () => tab(b.dataset.tab));
async function ask() {
  const txt = $("#mtxt").value.trim(); if (!txt) return; $("#mtxt").value = "";
  const add = (h, me) => { const d = document.createElement("div"); d.className = "msg " + (me ? "user" : "bot"); d.innerHTML = `<div class="b">${h}</div>`; $("#mchat").appendChild(d); return d; };
  add(esc(txt), true); const th = add(`<div class="skeleton"><div class="line"></div><div class="line"></div></div>`); th.dataset.busy = 1;
  try { const r = await api("/api/chat", {json: {text: txt}}); th.querySelector(".b").innerHTML = mdl(r.text); delete th.dataset.busy; }
  catch (e) { th.querySelector(".b").textContent = e.message; delete th.dataset.busy; }
}
$("#msend").onclick = ask; $("#mtxt").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } };
$("#logout").onclick = async () => { await fetch("/api/logout", {method: "POST"}); location.href = "/"; };
const TH_IC = {dark: "moon", light: "sun", contrast: "contrast"};
function themeIcon() { $("#theme").innerHTML = icon(TH_IC[A11Y.theme()]); }
$("#theme").onclick = () => { const th = A11Y.THEMES; A11Y.setTheme(th[(th.indexOf(A11Y.theme()) + 1) % th.length]); themeIcon(); };
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSide(); });
document.addEventListener("click", e => {
  const ch = e.target.closest("button.cite[data-doc]"); if (!ch) return;
  window.open(`/page/${encodeURIComponent(ch.dataset.doc)}/${ch.dataset.page}.png`, "_blank");
});
(async () => {
  document.querySelectorAll("[data-icon]").forEach(el => el.innerHTML = icon(el.dataset.icon));
  I18N = await (await fetch("/static/i18n.json")).json();
  ME = (await api("/api/me")).user;
  if (ME.role !== "manager") { location.href = "/"; return; }
  $$("[data-t]").forEach(el => el.textContent = t(el.dataset.t));
  themeIcon();
  await loadFleet(); renderFleet();
  setInterval(loadFleet, 30000);
})();
