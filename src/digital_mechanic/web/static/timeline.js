// Vehicle diagnostic timeline and the "before arrival" summary, shared by the mechanic and manager pages.
const _esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const _ic = (n, s = 16) => (typeof icon === "function" ? icon(n, s) : "");

function tlSpark(trend) {
  const pts = trend || []; if (pts.filter(p => p.anomaly_score != null).length < 2) return "";
  const W = 320, H = 56, n = pts.length;
  const xy = pts.map((p, i) => [8 + i * (W - 16) / Math.max(n - 1, 1), p.anomaly_score == null ? null : H - 8 - p.anomaly_score * (H - 16)]);
  const col = s => s === "anomaly" ? "var(--danger)" : s === "normal" ? "var(--ok)" : "var(--accent)";
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="anomaly score">` +
    `<line x1="0" x2="${W}" y1="${H - 8 - 0.5 * (H - 16)}" y2="${H - 8 - 0.5 * (H - 16)}" stroke="var(--border-2)" stroke-dasharray="3 4"/>` +
    `<polyline fill="none" stroke="var(--text-3)" stroke-width="1.5" vector-effect="non-scaling-stroke" points="${xy.filter(p => p[1] != null).map(p => p.join(",")).join(" ")}"/>` +
    xy.map((p, i) => `<circle cx="${p[0]}" cy="${p[1] ?? H - 8}" r="4" fill="${col(pts[i].status)}"><title>${_esc(pts[i].date || pts[i].session_id)} ${pts[i].anomaly_score ?? "—"}</title></circle>`).join("") + "</svg>";
}

function renderTimeline(tl) {
  const ic = s => s === "anomaly" ? _ic("triangle-alert") : s === "normal" ? _ic("circle-check") : _ic("rotate-ccw");
  let h = `<div class="spark">${tlSpark(tl.trend)}</div><div class="tl-sum small muted">` +
    (tl.days_since_last_check != null ? `<span>Последняя проверка ${_esc(tl.last_check)}, ${tl.days_since_last_check} дн. назад</span>` : "") +
    (tl.recurring.length ? `<span class="warn-text">Повторяется: ${tl.recurring.map(r => `${_esc(r.component || r.anomaly_type)} ×${r.count}`).join(", ")}</span>` : "") +
    (tl.mileage_note ? `<span>${_esc(tl.mileage_note)}</span>` : "") + `</div><div class="tl">`;
  for (const e of tl.entries) {
    const cls = e.status === "anomaly" ? "err" : e.status === "normal" ? "ok-text" : "";
    const top = `<span class="${cls}">${ic(e.status)}</span><span class="mono">${_esc(e.date)}</span><span class="muted">${_esc(e.status_ru)}${e.component ? ", " + _esc(e.component) : ""}</span>`;
    const sub = [e.anomaly_score != null ? `оценка ${e.anomaly_score}` : "", e.mileage_km ? `${e.mileage_km.toLocaleString("ru-RU")} км` : "",
      e.ticket ? `заявка ${_esc(e.ticket.ticket_id)}` : "", e.recurring ? "повтор" : "", e.demo ? "демо-история" : ""].filter(Boolean).join(", ");
    let body = `<div class="small muted" style="margin-top:8px">${_esc(e.session_id)}${e.confidence != null ? `, уверенность ${e.confidence}` : ""}, запись: ${_esc(({good: "хорошая", medium: "средняя", bad: "плохая"})[e.signal_quality] || e.signal_quality)}${e.mechanic ? `, механик ${_esc(e.mechanic)}` : ""}</div>`;
    if (e.description) body += `<div class="small" style="margin-top:4px">${_esc(e.description)}</div>`;
    if (e.repair) body += `<div class="small" style="margin-top:8px">Ремонт ${e.repair.done}/${e.repair.total}${e.repair.status === "done" ? ", завершён" : ""}<ol>${e.repair.steps_done.map(s => `<li>${_esc(s)}</li>`).join("")}</ol></div>`;
    if (e.parts && e.parts.length) body += `<div class="small">Запчасти: ${e.parts.map(p => _esc(p.name || p.text || p)).join(", ")}</div>`;
    if (e.recheck) body += `<div class="small">Повторная проверка ${_esc(e.recheck.date)}: <b>${_esc(e.recheck.status_ru)}</b>${e.recheck.anomaly_score != null ? ` (${e.recheck.anomaly_score})` : ""}</div>`;
    if (e.answer) body += `<div class="tl-ans">${_esc(e.answer).replace(/\n/g, "<br>")}</div>`;
    if (e.sources && e.sources.length) body += `<div style="margin-top:8px">${e.sources.map(s => `<button class="cite" data-doc="${_esc(s.doc_id)}" data-page="${s.page}">с. ${s.page}</button>`).join("")}</div>`;
    h += `<details class="tl-e"><summary><span class="tl-top">${top}</span>${sub ? `<span class="tl-sub">${sub}</span>` : ""}</summary>${body}</details>`;
  }
  return h + "</div>";
}

// "Before arrival": what is already known about the vehicle before the first question.
function arrivalCard(tl, tr) {
  tr = tr || (k => k);
  const es = tl.entries || []; if (!es.length) return "";
  const e = es[0], rows = [];
  const ago = tl.days_since_last_check != null ? `, ${tl.days_since_last_check} ${tr("days_ago")}` : "";
  rows.push(["activity", `${tr("last_check")}: <b>${_esc(e.date)}</b>${ago}, ${_esc(e.status_ru)}${e.anomaly_type ? ` ${_esc(e.anomaly_type)} ${_esc(e.component || "")}` : ""}`]);
  if ((tl.recurring || []).length) rows.push(["rotate-ccw", `${tr("recurring")}: ` + tl.recurring.map(r => `<b>${_esc(r.anomaly_type)}</b> ${_esc(r.component || "")} ×${r.count}`).join("; ")]);
  const open = es.find(x => x.ticket && !["closed", "returned"].includes(x.ticket.status));
  if (open) rows.push(["file-text", `${tr("open_ticket")}: <b>${_esc(open.ticket.ticket_id)}</b>`]);
  const ret = es.find(x => x.ticket && x.ticket.status === "returned");
  if (ret) rows.push(["forward", `${tr("ticket_returned")}: <b>${_esc(ret.ticket.ticket_id)}</b>${ret.ticket.review_comment ? ". " + _esc(ret.ticket.review_comment) : ""}`]);
  const rep = es.find(x => x.repair && x.repair.status === "open");
  if (rep) rows.push(["wrench", `${tr("repair_in_progress")}: ${rep.repair.done}/${rep.repair.total}`]);
  const last = es.find(x => x.repair && x.repair.status === "done");
  if (last) rows.push(["circle-check", `${tr("last_fix")} ${_esc(last.repair.date || last.date)}: ${_esc((last.repair.steps_done || []).slice(0, 2).join("; "))}` +
    (last.recheck ? `<br>${tr("recheck")}: ${_esc(last.recheck.status_ru)}` : "")]);
  return `<div class="arrival">${rows.map(([i, l]) => `<div class="r">${_ic(i)}<span>${l}</span></div>`).join("")}</div>`;
}
