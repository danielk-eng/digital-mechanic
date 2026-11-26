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
  b += `<div class="card"><b>${t("history")}</b><br>${chart(h.checks)}<table><tr><th>Проверка</th><th>Дата</th><th>Статус</th><th>Тип</th><th>Оценка</th><th>Уверенность</th><th>Запись</th></tr>` +
    h.checks.map(c => `<tr><td>${c.session_id}</td><td>${(c.checked_at || "").slice(0, 16).replace("T", " ")}</td><td>${c.status}</td><td>${esc((c.anomaly_type || "") + " " + (c.anomaly_title || ""))}</td><td>${c.anomaly_score ?? "—"}</td><td>${c.confidence ?? "—"}</td><td>${c.signal_quality}</td></tr>`).join("") + "</table></div>";
  b += `<div class="card"><b>${t("tickets")}</b>` + (h.tickets.length ? "<ul>" + h.tickets.map(x => `<li><b>${esc(x.ticket_id)}</b> ${esc(x.urgency)} · ${esc(x.at)} — ${esc(x.conclusion)}</li>`).join("") + "</ul>" : `<div class="muted">нет</div>`) + "</div>";
  if (h.repair) b += `<div class="card"><b>🛠 ${esc(h.repair.title)}</b> — ${h.repair.status === "done" ? t("repair_done") : `${t("step")} ${h.repair.current + 1} ${t("of")} ${h.repair.steps.length}`}<ol class="steps">${h.repair.steps.map((s, i) => `<li class="${s.done ? "done" : i === h.repair.current ? "cur" : ""}">${esc(s.text)}</li>`).join("")}</ol></div>`;
  $("#dBody").innerHTML = b; $("#drawer").classList.remove("hidden");
}
$("#dClose").onclick = () => $("#drawer").classList.add("hidden");

async function loadTickets() {
  const rows = []; // tickets come from the fleet endpoint per vehicle; aggregate via audit-safe endpoint
  const vs = FLEET.vehicles.filter(v => v.tickets);
  for (const v of vs) { const h = await api(`/api/fleet/${encodeURIComponent(v.vehicle_id)}`); h.tickets.forEach(x => rows.push({...x, vehicle_id: v.vehicle_id})); }
  $("#tab-tickets").innerHTML = rows.length ? `<table><tr><th>№</th><th>ТС</th><th>Приоритет</th><th>Тип</th><th>Создана</th><th>Диагноз</th></tr>` + rows.map(x => `<tr class="click" data-v="${x.vehicle_id}"><td><b>${esc(x.ticket_id)}</b></td><td>${esc(x.vehicle_id)}</td><td>${esc(x.urgency)}</td><td>${esc(x.anomaly_type)}</td><td>${esc(x.at)}</td><td>${esc(x.conclusion)}</td></tr>`).join("") + "</table>" : `<div class="muted">Заявок пока нет</div>`;
  document.querySelectorAll("#tab-tickets tr[data-v]").forEach(tr => tr.onclick = () => openVehicle(tr.dataset.v));
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
  ({tickets: loadTickets, repairs: loadRepairs, audit: loadAudit, econ: loadEcon}[name] || (() => {}))();
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
