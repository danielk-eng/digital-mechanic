"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let I18N = {}, ME = null, FLEET = null, FILTER = null;
const t = k => (I18N[(ME && ME.lang) || "ru"] || I18N.ru)[k] || I18N.ru[k] || k;
async function api(p, o = {}) { if (o.json) { o.method = "POST"; o.headers = {"Content-Type": "application/json"}; o.body = JSON.stringify(o.json); }
  const r = await fetch(p, {credentials: "same-origin", ...o}); if (r.status === 401 || r.status === 403) { location.href = "/"; throw new Error("auth"); } if (!r.ok) throw new Error(await r.text()); return r.json(); }
const ST = {ready: "ready", attention: "attention", quarantine: "quarantine"};
const badge = (st, rep) => `<span class="badge b-${st}">${t(st)}</span>` + (rep ? ` <span class="badge b-repair">🛠 ${t("in_repair")}</span>` : "");
const fmt = ts => new Date(ts * 1000).toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});

function chart(checks) {
  const W = 520, H = 160, n = checks.length; if (!n) return "";
  const x = i => 40 + i * (W - 60) / Math.max(n - 1, 1), y = v => H - 25 - v * (H - 45);
  const col = s => s === "anomaly" ? "#e5484d" : s === "normal" ? "#2fbf71" : "#3d8bfd";
  const pts = checks.map((c, i) => c.anomaly_score == null ? null : `${x(i)},${y(c.anomaly_score)}`).filter(Boolean).join(" ");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;background:#11181e;border-radius:10px">
    <line x1="35" x2="${W - 10}" y1="${y(0.5)}" y2="${y(0.5)}" stroke="#666" stroke-dasharray="4"/><text x="4" y="${y(0.5) + 4}" fill="#888" font-size="11">0.5</text>
    <text x="4" y="${y(1) + 4}" fill="#888" font-size="11">1.0</text><text x="4" y="${y(0) + 4}" fill="#888" font-size="11">0</text>
    <polyline fill="none" stroke="#ffb020" stroke-width="2.5" points="${pts}"/>` +
    checks.map((c, i) => `<circle cx="${x(i)}" cy="${c.anomaly_score == null ? y(0) : y(c.anomaly_score)}" r="6" fill="${col(c.status)}"><title>${c.session_id}: ${c.status} ${c.anomaly_title || ""} ${c.anomaly_score ?? "сбой записи"}</title></circle>
    <text x="${x(i) - 18}" y="${H - 6}" fill="#aaa" font-size="11">${(c.checked_at || "").slice(5, 10)}</text>`).join("") + "</svg>";
}

function spark(scores) {
  if (!scores || scores.length < 2) return "";
  const W = 90, H = 24, n = scores.length;
  return `<svg width="${W}" height="${H}"><polyline fill="none" stroke="#ffb020" stroke-width="2" points="${scores.map((s, i) => `${4 + i * (W - 8) / (n - 1)},${H - 3 - s * (H - 6)}`).join(" ")}"/></svg>`;
}

async function loadFleet() {
  FLEET = await api("/api/fleet");
  const c = FLEET.counts;
  $("#tiles").innerHTML = [["ready", "🟢"], ["attention", "🟡"], ["quarantine", "🔴"], ["in_repair", "🛠"], ["tickets", "📝"]].map(([k, ic]) =>
    `<div class="tile ${FILTER === k ? "on" : ""}" data-k="${k}"><div class="muted">${ic} ${t(k)}</div><div class="n">${c[k]}</div></div>`).join("");
  document.querySelectorAll(".tile").forEach(el => el.onclick = () => { FILTER = FILTER === el.dataset.k ? null : el.dataset.k; if (el.dataset.k === "tickets") return tab("tickets"); tab("fleet"); renderFleet(); loadFleetTiles(); });
  renderFleet();
}
function loadFleetTiles() { document.querySelectorAll(".tile").forEach(el => el.classList.toggle("on", el.dataset.k === FILTER)); }

function renderFleet() {
  const rows = FLEET.vehicles.filter(v => !FILTER || (FILTER === "in_repair" ? v.in_repair : v.status === FILTER));
  $("#tab-fleet").innerHTML = `<div class="muted">${rows.length} / ${FLEET.counts.total} · сортировка: карантин → внимание → норма, затем по оценке отклонения</div><table><tr><th>ТС</th><th>Модель / двигатель</th><th>Статус</th><th>Последняя проверка</th><th>Динамика</th><th>Приоритет</th><th>Заявки</th></tr>` +
    rows.map(v => { const l = v.last || {}; return `<tr class="click" data-v="${v.vehicle_id}"><td><b>${esc(v.vehicle_id)}</b><br><span class="muted">${esc(v.garage_no)}</span></td><td>${esc(v.model)}<br><span class="muted">${esc(v.engine_model)}</span></td><td>${badge(v.status, v.in_repair)}</td>
      <td>${esc(l.session_id)} · ${esc((l.checked_at || "").slice(0, 10))}<br><span class="muted">${l.status === "anomaly" ? esc(l.anomaly_type + " " + l.anomaly_title) + " · " + l.anomaly_score : l.status === "check_failed" ? "сбой записи — повторить" : "норма"}</span></td>
      <td>${spark(v.scores)}</td><td>${v.urgency ? `<span class="badge b-${v.urgency === "high" ? "quarantine" : v.urgency === "medium" ? "attention" : "ready"}">${v.urgency}</span>` : ""}</td><td>${v.tickets || ""}</td></tr>`; }).join("") + "</table>";
  document.querySelectorAll("tr[data-v]").forEach(tr => tr.onclick = () => openVehicle(tr.dataset.v));
}

async function openVehicle(vid) {
  const h = await api(`/api/fleet/${encodeURIComponent(vid)}`);
  const v = h.vehicle, row = FLEET.vehicles.find(r => r.vehicle_id === vid) || {};
  $("#dTitle").textContent = `${v.vehicle_id} · ${v.model && v.model.startsWith(v.make) ? v.model : (v.make + " " + (v.model || ""))} · ${v.engine_model}`;
  let b = `<div class="card">${badge(row.status, row.in_repair)} · тренд anomaly_score: <b>${esc(h.anomaly_score_trend)}</b>${row.urgency_basis && row.urgency_basis.length ? `<div class="muted">Приоритет ${row.urgency}: ${row.urgency_basis.map(esc).join("; ")}</div>` : ""}</div>`;
  try { const tl = await api(`/api/history/${encodeURIComponent(vid)}`); b += arrivalCard(tl, t) + `<div class="card"><b>${t("history")}</b>${renderTimeline(tl)}</div>`; }
  catch (_) { b += `<div class="card"><b>${t("history")}</b><br>${chart(h.checks)}</div>`; }
  b += `<div class="card"><b>${t("tickets")}</b>` + (h.tickets.length ? "<ul>" + h.tickets.map(x => `<li><b>${esc(x.ticket_id)}</b> ${esc(x.urgency)} · ${esc(x.at)} — ${esc(x.conclusion)}</li>`).join("") + "</ul>" : `<div class="muted">нет</div>`) + "</div>";
  if (h.repair) b += `<div class="card"><b>🛠 ${esc(h.repair.title)}</b> — ${h.repair.status === "done" ? t("repair_done") : `${t("step")} ${h.repair.current + 1} ${t("of")} ${h.repair.steps.length}`}<ol class="steps">${h.repair.steps.map((s, i) => `<li class="${s.done ? "done" : i === h.repair.current ? "cur" : ""}">${esc(s.text)}</li>`).join("")}</ol></div>`;
  $("#dBody").innerHTML = b; $("#drawer").classList.remove("hidden");
}
$("#dClose").onclick = () => $("#drawer").classList.add("hidden");

function pageLink(x) {
  return x.extra ? `<a class="chip" target="_blank" href="/page/${encodeURIComponent(x.doc_id)}/${x.page}.png">${esc(x.document.slice(0, 26))}, стр. ${x.page}</a>`
    : `<a class="chip" target="_blank" href="/manuals/${encodeURIComponent(x.doc_id)}.pdf#page=${x.page}">${esc(x.document.slice(0, 26))}, стр. ${x.page}</a>`;
}
function mdl(s) { return esc(s || "").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>"); }
function attHtml(a) {  // ticket package: summary, last answer, pages, photos
  if (!a) return `<div class="muted">Вложений нет (заявка создана до версии с пакетом)</div>`;
  return `<div class="att"><div>${mdl(a.summary)}</div>` + (a.answer ? `<details><summary>Ответ агента</summary><div>${mdl(a.answer)}</div></details>` : "") +
    (a.pages && a.pages.length ? `<div>📄 ${a.pages.map(pageLink).join(" ")}</div>` : "") +
    (a.inspection && a.inspection.length ? `<div>🔍 Осмотр: ${a.inspection.map(it => `${esc(it.component)} — ${esc(it.finding)}`).join("; ")}</div>` : "") +
    (a.photos && a.photos.length ? `<div>${a.photos.map(n => `<a target="_blank" href="/api/photo/${encodeURIComponent(n)}"><img class="thumb" src="/api/photo/${encodeURIComponent(n)}"></a>`).join("")}</div>` : "") + `</div>`;
}
async function loadTickets() {
  const r = await api("/api/tickets"); const rows = r.tickets;
  $("#tab-tickets").innerHTML = rows.length ? `<table><tr><th>№</th><th>ТС</th><th>Приоритет</th><th>Тип</th><th>Создана</th><th>Диагноз</th><th>Статус</th></tr>` + rows.map((x, i) =>
    `<tr class="click" data-i="${i}"><td><b>${esc(x.ticket_id)}</b> ${x.attachments ? "📎" : ""}</td><td>${esc(x.vehicle_id)}</td><td>${esc(x.urgency)}</td><td>${esc(x.anomaly_type)}</td><td>${esc(x.at)}</td><td>${esc(x.conclusion)}</td><td>${esc(TSTATUS[x.status] || x.status || "")}</td></tr>` +
    `<tr class="hidden det" id="tk${i}"><td colspan="7">${attHtml(x.attachments)}<div class="row" style="margin-top:.4em"><button onclick="openVehicle('${x.vehicle_id}')">🗂 Машина</button>${reviewHtml(x)}</div></td></tr>`).join("") + "</table>" : `<div class="muted">Заявок пока нет</div>`;
  document.querySelectorAll("#tab-tickets tr[data-i]").forEach(tr => tr.onclick = () => $("#tk" + tr.dataset.i).classList.toggle("hidden"));
  bindReview();
}
const TSTATUS = {created: "создана", approved: "утверждена", returned: "возвращена"};
function reviewHtml(x) {
  const rv = x.review ? `<span class="muted">${x.review.decision === "approved" ? "✅ утверждена" : "↩️ возвращена"} ${esc(x.review.by || "")}${x.review.comment ? ": " + esc(x.review.comment) : ""}</span>` : "";
  return rv + (x.status === "created" || x.status === "returned" ? `<button class="go" data-rv="${esc(x.ticket_id)}" data-d="approved">✅ Утвердить</button><button data-rv="${esc(x.ticket_id)}" data-d="returned">↩️ Вернуть с комментарием</button>` : "");
}
function bindReview() {
  document.querySelectorAll("#tab-tickets button[data-rv]").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    const comment = b.dataset.d === "returned" ? prompt("Что исправить механику?") : "";
    if (comment === null) return;
    await api(`/api/ticket/${encodeURIComponent(b.dataset.rv)}/review`, {json: {decision: b.dataset.d, comment}}); loadTickets(); loadFleetTiles && loadFleetTiles();
  });
}
async function loadHandoffs() {
  const r = await api("/api/handoffs");
  const st = {open: "🟠 новая", taken: "🔵 в работе у главного", closed: "✅ закрыта"};
  r.handoffs.forEach(h => { h.payload = Object.assign({pages: [], photos: [], dialog: [], items: []}, h.payload); });
  const sev = {"норма": "🟢", "внимание": "🟡", "дефект": "🔴"};
  $("#tab-handoffs").innerHTML = r.handoffs.length ? r.handoffs.map(h => `<div class="card">${h.kind === "inspection" ? "<b>🔍 Акт осмотра</b> " : ""}<div class="row"><b>${esc(h.vehicle_id)}</b><span class="muted">${new Date(h.ts * 1000).toLocaleString("ru-RU")} · ${esc(h.user_name)}</span><span>${st[h.status] || h.status}</span></div>` +
    (h.note ? `<div>💬 ${esc(h.note)}</div>` : "") + `<div style="margin:.4em 0">${mdl(h.summary)}</div>` +
    (h.payload.items.length ? `<ul>${h.payload.items.map(it => `<li>${sev[it.severity] || "🟡"} <b>${esc(it.component)}</b>: ${esc(it.finding)} <span class="muted">(${esc(it.source)})</span>${it.page ? " " + pageLink(it) : ""}</li>`).join("")}</ul>` : "") +
    (h.payload.check ? `<div class="muted">ML: ${esc(h.payload.check.status)} ${esc(h.payload.check.anomaly_type || "")} · score ${h.payload.check.anomaly_score ?? "—"} · conf ${h.payload.check.confidence ?? "—"}</div>` : "") +
    (h.payload.pages.length ? `<div>📄 ${h.payload.pages.map(pageLink).join(" ")}</div>` : "") +
    (h.payload.photos.length ? `<div>${h.payload.photos.map(n => `<a target="_blank" href="/api/photo/${encodeURIComponent(n)}"><img class="thumb" src="/api/photo/${encodeURIComponent(n)}"></a>`).join("")}</div>` : "") +
    (h.payload.dialog.length ? `<details><summary>Диалог (${h.payload.dialog.length})</summary>` : "<span>") + `${h.payload.dialog.map(d => `<div class="msg ${d.role === "user" ? "user" : "bot"}"><div class="b">${mdl(d.text)}</div></div>`).join("")}` + (h.payload.dialog.length ? "</details>" : "</span>") +
    `<div class="row">${h.status === "open" ? `<button data-h="${h.id}" data-s="taken">Взять в работу</button>` : ""}${h.status !== "closed" ? `<button data-h="${h.id}" data-s="closed">Закрыть</button>` : ""}<button onclick="openVehicle('${h.vehicle_id}')">🗂 Машина</button></div></div>`).join("")
    : `<div class="muted">Пока никто не передавал случаи главному</div>`;
  document.querySelectorAll("#tab-handoffs button[data-h]").forEach(b => b.onclick = async () => {
    const comment = b.dataset.s === "closed" ? (prompt("Комментарий (необязательно)") || "") : "";
    await api(`/api/handoff/${b.dataset.h}`, {json: {status: b.dataset.s, comment}}); loadHandoffs(); });
}
async function loadRepairs() {
  const vs = FLEET.vehicles.filter(v => v.in_repair);
  let h = vs.length ? "" : `<div class="muted">Сейчас нет машин в ремонте</div>`;
  for (const v of vs) { const d = await api(`/api/fleet/${encodeURIComponent(v.vehicle_id)}`); const r = d.repair; if (!r) continue;
    const done = r.steps.filter(s => s.done).length; const mins = Math.round((Date.now() / 1000 - r.started) / 60);
    h += `<div class="card click" onclick="openVehicle('${v.vehicle_id}')"><b>${esc(v.vehicle_id)}</b> · ${esc(r.title)} · ${done}/${r.steps.length} шагов · ${mins} мин в работе<div style="background:#26333d;border-radius:6px;height:10px;margin-top:.4em"><div style="background:var(--go);height:10px;border-radius:6px;width:${100 * done / r.steps.length}%"></div></div></div>`; }
  $("#tab-repairs").innerHTML = h;
}
async function loadAudit() {
  const r = await api("/api/audit?limit=300");
  $("#tab-audit").innerHTML = `<div class="row"><a class="chip" href="/api/audit.csv">⬇ CSV</a><span class="muted">ПДн маскируются до записи; срок хранения ограничен (AUDIT_RETENTION_DAYS)</span></div><table><tr><th>Время</th><th>Кто</th><th>ТС</th><th>Действие</th><th>Запрос</th><th>Ответ</th><th>Инструменты</th><th>Источники</th><th>мс</th></tr>` +
    r.rows.map(a => `<tr><td>${fmt(a.ts)}</td><td>${esc(a.user_name || a.user_id)}<br><span class="muted">${esc(a.role)}</span></td><td>${esc(a.vehicle_id)}</td><td>${esc(a.action)}${a.ok ? "" : " ⚠️"}</td><td>${esc((a.input || "").slice(0, 120))}</td><td>${esc((a.output || "").slice(0, 160))}</td><td class="muted">${Array.isArray(a.tools) ? a.tools.map(x => esc(x.tool)).join(", ") : ""}</td><td class="muted">${Array.isArray(a.sources) ? a.sources.map(esc).join("; ") : ""}</td><td>${a.latency_ms ?? ""}</td></tr>`).join("") + "</table>";
}
async function loadEcon() {
  const u = await api("/api/usage");
  const per = u.calls ? (u.prompt_tokens + u.completion_tokens) / u.calls : 0;
  $("#tab-econ").innerHTML = `<b>Расход LLM с момента запуска</b><div class="kv"><span class="muted">Вызовов LLM</span><span>${u.calls}</span><span class="muted">Токены вход/выход</span><span>${u.prompt_tokens} / ${u.completion_tokens}</span><span class="muted">Среднее время вызова</span><span>${u.avg_seconds} с</span><span class="muted">Токенов на вызов</span><span>${Math.round(per)}</span></div><p class="muted">Методика расчёта стоимости — в docs/ЭКОНОМИКА.md.</p>`;
}

function tab(name) {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll("section[id^=tab-]").forEach(s => s.classList.toggle("hidden", s.id !== "tab-" + name));
  ({tickets: loadTickets, repairs: loadRepairs, audit: loadAudit, econ: loadEcon, handoffs: loadHandoffs}[name] || (() => {}))();
}
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => tab(b.dataset.tab));

async function mask() {
  const txt = $("#mtxt").value.trim(); if (!txt) return; $("#mtxt").value = "";
  const add = (h, me) => { const d = document.createElement("div"); d.className = "msg " + (me ? "user" : "bot"); d.innerHTML = `<div class="b">${h}</div>`; $("#mchat").appendChild(d); return d; };
  add(esc(txt), true); const th = add(`<span class="thinking">${t("thinking")}</span>`);
  try { const r = await api("/api/chat", {json: {text: txt}}); th.querySelector(".b").innerHTML = esc(r.text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>"); }
  catch (e) { th.querySelector(".b").textContent = e.message; }
}
$("#msend").onclick = mask; $("#mtxt").onkeydown = e => { if (e.key === "Enter") mask(); };
$("#logout").onclick = async () => { await fetch("/api/logout", {method: "POST"}); location.href = "/"; };

(async () => {
  I18N = await (await fetch("/static/i18n.json")).json();
  ME = (await api("/api/me")).user;
  if (ME.role !== "manager") { location.href = "/"; return; }
  document.querySelectorAll("[data-t]").forEach(el => el.textContent = t(el.dataset.t));
  await loadFleet();
  setInterval(loadFleet, 30000);
})();

// manual page links inside the timeline: open the page image (or the manual text page) in a new tab
document.addEventListener("click", e => {
  const ch = e.target.closest(".chip[data-doc]"); if (!ch) return;
  window.open(`/page/${encodeURIComponent(ch.dataset.doc)}/${ch.dataset.page}.png`, "_blank");
});
