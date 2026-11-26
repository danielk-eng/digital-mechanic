// Vehicle diagnostic timeline, shared by mechanic and manager pages. Needs esc() from the page script.
function tlSpark(trend) {
  const pts = (trend || []); if (pts.filter(p => p.anomaly_score != null).length < 2) return "";
  const W = 260, H = 56, n = pts.length;
  const xy = pts.map((p, i) => [10 + i * (W - 20) / Math.max(n - 1, 1), p.anomaly_score == null ? null : H - 8 - p.anomaly_score * (H - 16)]);
  const col = s => s === "anomaly" ? "#e5484d" : s === "normal" ? "#2fbf71" : "#3d8bfd";
  return `<svg width="${W}" height="${H}" style="background:#11181e;border-radius:8px;max-width:100%"><polyline fill="none" stroke="#ffb020" stroke-width="2" points="${xy.filter(p => p[1] != null).map(p => p.join(",")).join(" ")}"/>` +
    xy.map((p, i) => `<circle cx="${p[0]}" cy="${p[1] ?? H - 8}" r="4" fill="${col(pts[i].status)}"><title>${pts[i].date} ${pts[i].session_id} ${pts[i].anomaly_score ?? "—"}</title></circle>`).join("") + "</svg>";
}
function renderTimeline(tl) {
  const icon = s => s === "anomaly" ? "⚠️" : s === "normal" ? "✅" : "🔁";
  let h = `<div class="tl-head">${tlSpark(tl.trend)}<div>` +
    (tl.days_since_last_check != null ? `<div><b>${tl.days_since_last_check}</b> дн. с последней проверки (${esc(tl.last_check)})</div>` : "") +
    (tl.recurring.length ? `<div class="tl-rec">🔁 Повторяется: ${tl.recurring.map(r => `${esc(r.component || r.anomaly_type)} ×${r.count}`).join(", ")}</div>` : "") +
    `<div class="muted">${esc(tl.mileage_note)}</div></div></div><div class="tl">`;
  for (const e of tl.entries) {
    const sum = `${icon(e.status)} <b>${esc(e.date)}</b> · ${esc(e.status_ru)}${e.component ? " · " + esc(e.component) : ""}` +
      (e.anomaly_score != null ? ` · score <b>${e.anomaly_score}</b>` : "") + (e.mileage_km ? ` · ${e.mileage_km.toLocaleString("ru-RU")} км` : "") +
      (e.ticket ? ` · 📝 ${esc(e.ticket.ticket_id)} (${esc(e.ticket.status)})` : "") + (e.recurring ? ` <span class="tl-badge">повтор</span>` : "") + (e.demo ? ` <span class="muted">демо</span>` : "");
    let body = `<div class="muted">${esc(e.session_id)}${e.confidence != null ? ` · уверенность ${e.confidence}` : ""} · запись: ${esc(e.signal_quality)}${e.mechanic ? ` · механик ${esc(e.mechanic)}` : ""}</div>`;
    if (e.description) body += `<div>ML: ${esc(e.description)}</div>`;
    if (e.repair) body += `<div>🛠 Ремонт ${e.repair.done}/${e.repair.total}${e.repair.status === "done" ? " — завершён" : ""}:<ol class="steps">${e.repair.steps_done.map(s => `<li class="done">${esc(s)}</li>`).join("")}</ol></div>`;
    if (e.parts && e.parts.length) body += `<div>🔩 Запчасти: ${e.parts.map(esc).join(", ")}</div>`;
    if (e.recheck) body += `<div>↩️ Повторная проверка ${esc(e.recheck.date)}: <b>${esc(e.recheck.status_ru)}</b>${e.recheck.anomaly_score != null ? ` (score ${e.recheck.anomaly_score})` : ""}</div>`;
    if (e.answer) body += `<div class="tl-ans">${esc(e.answer).replace(/\n/g, "<br>")}</div>`;
    if (e.sources && e.sources.length) body += `<div>${e.sources.map(s => `<span class="chip" data-doc="${esc(s.doc_id)}" data-page="${s.page}">${esc((s.document || "").slice(0, 30))}, стр. ${s.page}</span>`).join("")}</div>`;
    h += `<details class="tl-e ${e.status}${e.recurring ? " rec" : ""}"><summary>${sum}</summary>${body}</details>`;
  }
  return h + "</div>";
}

// Items 10-11: "service book / pre-arrival" card — what is already known about the vehicle before the first question.
function arrivalCard(tl, tr) {
  tr = tr || (k => k);
  const es = tl.entries || [];
  if (!es.length) return "";
  const e = es[0], esc_ = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const lines = [];
  const ago = tl.days_since_last_check != null ? ` (${tl.days_since_last_check} ${tr("days_ago")})` : "";
  lines.push(`🩺 ${tr("last_check")}: <b>${esc_(e.date)}</b>${ago} — ${esc_(e.status_ru)}${e.anomaly_type ? ` ${esc_(e.anomaly_type)} ${esc_(e.component || "")}` : ""}${e.anomaly_score != null ? ` · score ${(+e.anomaly_score).toFixed(2)}` : ""}`);
  if ((tl.recurring || []).length) lines.push(`🔁 ${tr("recurring")}: ` + tl.recurring.map(r => `<b>${esc_(r.anomaly_type)}</b> ${esc_(r.component || "")} ×${r.count}`).join("; "));
  const open = es.find(x => x.ticket && !["closed", "returned"].includes(x.ticket.status));
  if (open) lines.push(`📝 ${tr("open_ticket")}: <b>${esc_(open.ticket.ticket_id)}</b> (${esc_(open.ticket.status)})`);
  const rep = es.find(x => x.repair && x.repair.status === "open");
  if (rep) lines.push(`🛠 ${tr("repair_in_progress")}: ${rep.repair.done}/${rep.repair.total}`);
  const last = es.find(x => x.repair && x.repair.status === "done");
  if (last) lines.push(`✅ ${tr("last_fix")}: ${esc_(last.repair.date || last.date)} — ${esc_((last.repair.steps_done || []).slice(0, 3).join("; "))}` +
    (last.parts && last.parts.length ? ` · ${esc_(last.parts.map(p => p.name || p.text || p).join(", "))}` : "") +
    (last.recheck ? ` · ${tr("recheck")}: ${esc_(last.recheck.status_ru)}` : ""));
  return `<div class="arrival"><b>📋 ${tr("arrival")}</b>${lines.map(l => `<div>${l}</div>`).join("")}</div>`;
}
