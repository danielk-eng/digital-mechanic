"use strict";
// Engineer menu: live request log with a per-request trace, MCP servers, model/STT settings, health of the ML/TOiR stubs.
// Every call is checked on the server (role "engineer"); keys are never shown or stored here.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
let ME = null, LAST = 0, ROWS = [], POLL = 0, COS = [], LCO = "";
async function api(p, o = {}) {
  if (o.json) { o.method = "POST"; o.headers = {"Content-Type": "application/json"}; o.body = JSON.stringify(o.json); }
  const r = await fetch(p, {credentials: "same-origin", ...o});
  if (r.status === 401 || r.status === 403) { location.href = "/"; throw new Error("auth"); }
  if (!r.ok) throw new Error(await r.text()); return r.json();
}
const time = ts => new Date(ts * 1000).toLocaleTimeString("ru-RU");
async function health() {
  const h = await api("/api/eng/health"), s = h.services;
  const k = (l, ok, sub) => `<div class="kpi" style="cursor:default"><div class="l">${l}</div><div class="n" style="font-size:1.25rem"><span class="pill ${ok ? "ready" : "quarantine"}">${ok ? "работает" : "нет ответа"}</span></div><div class="s">${sub}</div></div>`;
  $("#health").innerHTML = k("ML-заглушка", s.ml.ok, `${s.ml.ms} мс`) + k("ТОиР-заглушка", s.toir.ok, `${s.toir.ms} мс`) +
    k("Языковая модель", s.llm.ok, `${esc(s.llm.model)}, вызовов ${s.llm.usage.calls}`) + k("Распознавание речи", true, `${esc(s.stt.order)}: ${Object.entries(s.stt.engines).filter(e => e[1]).map(e => e[0]).join(", ")}`) +
    k("Индекс руководств", true, `${s.index.pages} + ${s.index.extra_pages} стр.`) +
    (h.errors_24h.length ? `<div class="kpi" style="cursor:default"><div class="l">Ошибки за сутки</div><div class="s">${h.errors_24h.map(e => `${esc(e.action)}: ${e.n}`).join(", ")}</div></div>` : "");
}
async function logs() {
  const r = await api(`/api/eng/logs?after=${LAST}&limit=200`);
  ROWS = [...r.rows, ...ROWS].slice(0, 300); LAST = r.last;
  if ($("#tab-logs").classList.contains("hidden")) return;
  if (!COS.length) { try { COS = (await api("/api/eng/companies")).companies; } catch (_) {} }
  const rows = ROWS.filter(a => !LCO || (LCO === "-" ? !a.company_id : a.company_id === LCO));
  $("#tab-logs").innerHTML = `<div class="filters"><select class="input" id="l_co" aria-label="Компания" style="max-width:260px;min-height:40px"><option value="">Все компании</option>` +
    COS.map(c => `<option value="${esc(c.id)}" ${LCO === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("") + `<option value="-" ${LCO === "-" ? "selected" : ""}>Инженеры</option></select>` +
    `<span class="small muted" style="align-self:center">Обновляется каждые 3 с. Нажмите на строку, чтобы открыть трассу запроса.</span></div>` +
    `<div class="panel" style="overflow:auto"><table class="tbl"><thead><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Запрос</th><th class="hide-m">Инструменты</th><th>мс</th></tr></thead><tbody>` +
    rows.map(a => `<tr class="click" data-id="${a.id}"><td class="sub">${time(a.ts)}</td><td>${esc(a.user_name || "")}<div class="sub">${esc(a.role || "")}</div></td>` +
      `<td>${esc(a.action)}${a.ok ? "" : ` <span class="err">ошибка</span>`}<div class="sub mono">${esc(a.vehicle_id || "")}</div></td><td class="small">${esc(a.input)}</td>` +
      `<td class="hide-m sub">${(a.tools || []).map(esc).join(", ")}</td><td class="num">${a.latency_ms ?? ""}</td></tr>`).join("") + `</tbody></table></div>`;
  $$("#tab-logs tr[data-id]").forEach(tr => tr.onclick = () => trace(tr.dataset.id));
  $("#l_co").onchange = e => { LCO = e.target.value; logs(); };
}
function side(title, html) {
  $("#layer").innerHTML = `<div class="scrim" id="scrim"></div><aside class="side" role="dialog" aria-modal="true"><div class="vhd"><b>${esc(title)}</b><button class="icon-btn" id="sClose" aria-label="close">${icon("x")}</button></div><div class="vbd">${html}</div></aside>`;
  $("#scrim").onclick = $("#sClose").onclick = () => $("#layer").innerHTML = "";
}
async function trace(id) {
  const r = await api(`/api/eng/trace/${id}`);
  side(`Запрос #${id}`, `<p class="small muted">${time(r.ts)}, ${esc(r.user_name || r.user_id)} (${esc(r.role)}), ${esc(r.action)}, ${r.latency_ms ?? "—"} мс, токенов ${r.tokens ?? 0}, ${r.ok ? "успешно" : "ошибка"}</p>` +
    `<div class="section-label">Вход</div><pre class="trace">${esc(r.input)}</pre><div class="section-label">Ответ</div><pre class="trace">${esc(r.output)}</pre>` +
    `<div class="section-label">Вызовы инструментов (${(r.tools || []).length})</div><pre class="trace">${esc(JSON.stringify(r.tools, null, 1))}</pre>` +
    `<div class="section-label">Источники</div><pre class="trace">${esc((r.sources || []).join("\n"))}</pre>`);
}
async function mcp() {
  const r = await api("/api/eng/mcp");
  $("#tab-mcp").innerHTML = `<div class="panel"><div class="pad"><div class="mcp-form">` +
    `<div><label class="label" for="m_name">Название</label><input id="m_name" class="input" placeholder="toir-prod"></div><div><label class="label" for="m_url">URL</label><input id="m_url" class="input" placeholder="https://…/mcp"></div>` +
    `<div><label class="label" for="m_tr">Транспорт</label><select id="m_tr" class="input"><option>http</option><option>sse</option></select></div>` +
    `<div><label class="label" for="m_env">Ключ из env</label><input id="m_env" class="input" placeholder="TOIR_MCP_TOKEN"></div><button class="btn primary" id="m_add">Добавить</button></div>` +
    `<p class="small muted" style="margin-top:8px">Сам ключ не вводится: укажите имя переменной окружения на стенде.</p></div></div>` +
    `<div class="panel"><table class="tbl"><thead><tr><th>Сервер</th><th>Статус</th><th class="hide-m">Инструменты</th><th></th></tr></thead><tbody>` +
    (r.servers.length ? r.servers.map(m => `<tr><td><b>${esc(m.name)}</b><div class="sub mono">${esc(m.url)}</div><div class="sub">${esc(m.transport)}${m.token_env ? ", ключ: " + esc(m.token_env) : ""}</div></td>` +
      `<td>${m.enabled ? "" : `<span class="pill attention">выключен</span> `}${esc(m.status || "не проверялся")}</td><td class="hide-m sub">${(m.tools || []).map(esc).join(", ")}</td>` +
      `<td><button class="btn ghost sm" data-chk="${m.id}">Проверить</button><button class="btn ghost sm" data-tog="${m.id}" data-on="${m.enabled ? 0 : 1}">${m.enabled ? "Выключить" : "Включить"}</button><button class="btn ghost sm" data-del="${m.id}">Удалить</button></td></tr>`).join("")
      : `<tr><td colspan="4" class="muted">Серверов пока нет</td></tr>`) + `</tbody></table></div>`;
  $("#m_add").onclick = async () => { try { await api("/api/eng/mcp", {json: {name: $("#m_name").value, url: $("#m_url").value, transport: $("#m_tr").value, token_env: $("#m_env").value}}); mcp(); } catch (e) { alert(e.message); } };
  $$("[data-chk]").forEach(b => b.onclick = async () => { b.disabled = true; await api(`/api/eng/mcp/${b.dataset.chk}/check`, {method: "POST"}); mcp(); });
  $$("[data-tog]").forEach(b => b.onclick = async () => { await api(`/api/eng/mcp/${b.dataset.tog}`, {json: {enabled: b.dataset.on === "1"}}); mcp(); });
  $$("[data-del]").forEach(b => b.onclick = async () => { if (confirm("Удалить сервер?")) { await api(`/api/eng/mcp/${b.dataset.del}`, {json: {delete: true}}); mcp(); } });
}
async function models() {
  const r = await api("/api/eng/models"), c = r.current;
  const inp = (k, l, v) => `<div class="field"><label class="label" for="k_${k}">${l}</label><input id="k_${k}" class="input" value="${esc(v ?? "")}"></div>`;
  $("#tab-models").innerHTML = `<div class="panel"><div class="pad">` +
    `<div class="field"><label class="label" for="k_llm_model">Языковая модель</label><input id="k_llm_model" class="input" list="llm_opts" value="${esc(c.llm_model)}"><datalist id="llm_opts">${r.llm_options.map(o => `<option value="${esc(o)}">`).join("")}</datalist></div>` +
    inp("llm_vision_model", "Модель для фото (пусто = та же)", c.llm_vision_model) + inp("llm_timeout_s", "Таймаут, с", c.llm_timeout_s) + inp("llm_max_tool_rounds", "Раундов инструментов", c.llm_max_tool_rounds) +
    `<div class="field"><label class="label" for="k_stt_order">Порядок распознавания речи</label><select id="k_stt_order" class="input">${r.stt_orders.map(o => `<option ${o === c.stt_order ? "selected" : ""}>${o}</option>`).join("")}</select></div>` +
    inp("deepgram_model", "Облачная модель STT", c.deepgram_model) +
    `<p class="small muted">Доступно: ${Object.entries(r.stt_engines).filter(e => e[1]).map(e => e[0]).join(", ")}. Облаком распознаются: ${r.deepgram_langs.join(", ")}; без локальной модели: ${r.no_whisper.join(", ")}. Ключи задаются только в окружении стенда.</p>` +
    `<div class="card-actions"><button class="btn primary" id="k_save">Применить</button></div></div></div>`;
  $("#k_save").onclick = async () => {
    const values = {}; Object.keys(c).forEach(k => { const el = $("#k_" + k); if (el) values[k] = el.value; });
    try { await api("/api/eng/models", {json: {values}}); models(); health(); } catch (e) { alert(e.message); }
  };
}
function speak(text) {
  if (!window.speechSynthesis || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.lang = "ru-RU"; u.rate = 1.05;
  const v = speechSynthesis.getVoices().find(x => x.lang.startsWith("ru")); if (v) u.voice = v;
  speechSynthesis.speak(u);
}
// Voice, same as the chief's agent tab: record -> /api/stt (server STT with the data-law routing) -> ask(); replies are spoken
let EVOICE = false, EREC = null;
async function micToggle() {
  const b = $("#emic");
  if (EREC) { EREC.stop(); return; }
  let stream; try { stream = await navigator.mediaDevices.getUserMedia({audio: true}); } catch (_) { alert("Нет доступа к микрофону"); return; }
  const chunks = [], rec = new MediaRecorder(stream); EREC = rec; EVOICE = true; b.classList.add("on");
  rec.ondataavailable = e => e.data.size && chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach(x => x.stop()); EREC = null; b.classList.remove("on");
    const fd = new FormData(); fd.append("audio", new Blob(chunks, {type: rec.mimeType || "audio/webm"}), "q.webm"); fd.append("lang", (ME && ME.lang) || "ru");
    try { const r = await api("/api/stt", {method: "POST", body: fd}); if (r.text) ask(r.text); } catch (e) { alert(e.message); }
  };
  rec.start();
  setTimeout(() => { if (EREC === rec) rec.stop(); }, window.__micMs || 6000);
}
async function ask(textIn) {
  const txt = (typeof textIn === "string" ? textIn : $("#etxt").value).trim(); if (!txt) return; $("#etxt").value = "";
  const add = (h, me) => { const d = document.createElement("div"); d.className = "msg " + (me ? "user" : "bot"); d.innerHTML = `<div class="b">${h}</div>`; $("#echat").appendChild(d); return d; };
  add(esc(txt), true); const th = add(`<div class="skeleton"><div class="line"></div></div>`); th.dataset.busy = 1;
  try { const r = await api("/api/chat", {json: {text: txt}}); th.querySelector(".b").innerHTML = esc(r.text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
    if (EVOICE) speak(r.speak || r.text.replace(/\*\*|\[[SX]\d+\]/g, "")); }
  catch (e) { th.querySelector(".b").textContent = e.message; }
  delete th.dataset.busy;
}
// ---------------------------------------------------------------- companies (tenants): the engineer sees and manages all
const PRIM = {"": "как на стенде", auto: "Cloud.ru для РФ-региона (152-ФЗ)", tokenrouter: "основная модель", cloudru: "Cloud.ru (РФ)"};
async function companies() {
  const r = await api("/api/eng/companies"); COS = r.companies;
  const vOwner = {}; r.companies.forEach(c => c.vehicles.forEach(v => vOwner[v] = c.name));
  $("#tab-companies").innerHTML = `<div class="panel"><div class="pad"><div class="mcp-form"><div><label class="label" for="c_name">Новая компания</label><input id="c_name" class="input" autocomplete="off"></div>` +
    `<div><label class="label" for="c_prim">Языковая модель</label><select id="c_prim" class="input">${r.llm_primary.map(o => `<option value="${o}">${esc(PRIM[o] || o)}</option>`).join("")}</select></div>` +
    `<button class="btn primary" id="c_add">Создать</button></div></div></div>` +
    `<div class="panel"><table class="tbl"><thead><tr><th>Компания</th><th>Главный механик</th><th class="hide-m">Машин / людей</th><th class="hide-m">Расход LLM</th><th>Модель</th></tr></thead><tbody>` +
    r.companies.map(c => `<tr class="click" data-c="${esc(c.id)}"><td><b>${esc(c.name)}</b><div class="sub mono">${esc(c.id)}</div>${c.active ? "" : `<span class="pill attention">выключена</span>`}</td>` +
      `<td>${c.chiefs.length ? c.chiefs.map(x => `${esc(x.name)} <span class="sub mono">${esc(x.login || "")}</span>`).join("<br>") : `<span class="muted">нет</span>`}</td>` +
      `<td class="hide-m num">${c.vehicles.length} / ${c.staff}</td><td class="hide-m small">${c.usage.calls} запросов, ${c.usage.tokens} ток.<div class="sub">${c.usage.cost_rub} ₽, ср. ${c.usage.avg_seconds} с</div></td>` +
      `<td class="small">${esc(PRIM[c.llm_primary || ""] || c.llm_primary)}</td></tr>`).join("") + `</tbody></table></div>`;
  $("#c_add").onclick = async () => { const name = $("#c_name").value.trim(); if (!name) return;
    try { await api("/api/eng/companies", {json: {name, llm_primary: $("#c_prim").value}}); companies(); } catch (e) { alert(e.message); } };
  $$("#tab-companies tr[data-c]").forEach(tr => tr.onclick = () => companyCard(r, tr.dataset.c, vOwner));
}
function companyCard(r, id, vOwner) {
  const c = r.companies.find(x => x.id === id);
  side(c.name, `<div class="field"><label class="label" for="cc_name">Название</label><input id="cc_name" class="input" value="${esc(c.name)}"></div>` +
    `<div class="field"><label class="label" for="cc_prim">Языковая модель для компании</label><select id="cc_prim" class="input">${r.llm_primary.map(o => `<option value="${o}" ${o === (c.llm_primary || "") ? "selected" : ""}>${esc(PRIM[o] || o)}</option>`).join("")}</select></div>` +
    `<label class="switch"><span>Компания активна</span><input type="checkbox" id="cc_act" ${c.active ? "checked" : ""}></label>` +
    `<div class="field"><span class="label">Машины компании (отметка переносит машину сюда)</span><div class="vpick">${r.all_vehicles.map(v => `<label><input type="checkbox" value="${esc(v)}" ${c.vehicles.includes(v) ? "checked disabled" : ""}> <span class="mono">${esc(v)}</span>${c.vehicles.includes(v) ? "" : ` <span class="sub">${esc(vOwner[v] || "")}</span>`}</label>`).join("")}</div></div>` +
    `<div class="card-actions"><button class="btn primary" id="cc_save">Сохранить</button></div>` +
    `<div class="section-label">Главный механик</div>` + (c.chiefs.length ? `<p class="small">${c.chiefs.map(x => `${esc(x.name)}, код <b class="mono">${esc(x.login || "")}</b>`).join("<br>")}</p>` : "") +
    `<div class="field"><label class="label" for="ch_name">Имя</label><input id="ch_name" class="input" autocomplete="off"></div>` +
    `<div class="field"><label class="label" for="ch_pin">PIN (4–8 цифр, пусто — придумаем)</label><input id="ch_pin" class="input" inputmode="numeric" autocomplete="off"></div>` +
    `<div class="field"><label class="label" for="ch_email">Почта</label><input id="ch_email" class="input" type="email" autocomplete="off"></div>` +
    `<div class="field"><label class="label" for="ch_form">Обращение</label><select id="ch_form" class="input"><option value="vy">на «вы»</option><option value="ty">на «ты»</option></select></div>` +
    `<div class="field"><label class="label" for="ch_style">Как помощник с ним говорит</label><textarea id="ch_style" class="input" rows="3" style="padding:12px 16px"></textarea></div>` +
    `<div class="card-actions"><button class="btn primary" id="ch_go">Создать главного механика</button></div><div id="ch_out"></div>`);
  $("#cc_save").onclick = async () => {
    try { await api(`/api/eng/companies/${id}`, {json: {name: $("#cc_name").value.trim(), llm_primary: $("#cc_prim").value, active: $("#cc_act").checked,
      vehicles: $$(".vpick input:checked:not(:disabled)").map(x => x.value)}}); $("#layer").innerHTML = ""; companies(); } catch (e) { alert(e.message); }
  };
  $("#ch_go").onclick = async () => {
    const name = $("#ch_name").value.trim(); if (!name) return;
    try {
      const x = await api(`/api/eng/companies/${id}/chief`, {json: {name, pin: $("#ch_pin").value.trim(), email: $("#ch_email").value.trim(), formality: $("#ch_form").value, style: $("#ch_style").value.trim()}});
      $("#ch_out").innerHTML = `<div class="panel"><div class="pad"><p>Создан: код <b class="mono">${esc(x.login)}</b>, PIN <b class="mono">${esc(x.pin)}</b></p><p class="small muted">PIN показан один раз.</p><div id="ch_qr"></div></div></div>`;
      QRLink.mount($("#ch_qr"), {userId: x.user.id, email: x.user.email}); $("#ch_go").disabled = true; companies();
    } catch (e) { $("#ch_out").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  };
}
function tab(name) {
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $$("section[id^=tab-]").forEach(s => s.classList.toggle("hidden", s.id !== "tab-" + name));
  ({logs, mcp, models, companies}[name] || (() => {}))();
}
$$("#tabs button").forEach(b => b.onclick = () => tab(b.dataset.tab));
$("#esend").onclick = () => ask(); $("#emic").onclick = micToggle; $("#etxt").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } };
$("#myqr").onclick = () => { side("Мой вход по QR", `<div id="my_qr"></div>`); QRLink.mount($("#my_qr"), {email: ME.email}); };
$("#logout").onclick = async () => { await fetch("/api/logout", {method: "POST"}); location.href = "/"; };
(async () => {
  document.querySelectorAll("[data-icon]").forEach(el => el.innerHTML = icon(el.dataset.icon));
  ME = (await api("/api/session")).user;
  if (!ME || ME.role !== "engineer") { location.href = ME && ME.role === "manager" ? "/manager" : "/"; return; }
  $("#whoami").textContent = `${ME.name}, инженер`;
  await health(); await logs();
  POLL = setInterval(() => { logs(); }, 3000); setInterval(health, 30000);
  const q = new URLSearchParams(location.search).get("tab"); if (q) tab(q);
})();
