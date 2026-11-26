"use strict";
// Device linking by QR (all roles): a 24 h link for one person; opening it asks for that person's PIN, then the usual long
// session starts on the new device. «Обновить» replaces the link (the old one stops working), «Отозвать» kills it.
(function () {
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  async function call(path, body) {
    const r = await fetch(path, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText); return d;
  }
  let D = null;  // strings in 9 languages: /static/qr_i18n.json
  const dict = async () => D || (D = await fetch("/static/qr_i18n.json").then(r => r.json()).catch(() => ({ru: {}})));
  let L = "ru";
  const T = (k, e) => ((D[L] && D[L][k]) || (D.ru && D.ru[k]) || k).replace("{e}", e || "");
  const until = ts => new Date(ts * 1000).toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
  // box: element to render into; opts: {userId, email, name}
  async function mount(box, opts = {}) {
    await dict(); L = opts.lang || document.documentElement.lang || "ru"; if (!D[L]) L = "ru";
    const who = opts.userId ? {user_id: opts.userId} : {};
    let cur = null;
    const intro = `<p class="small muted">${T(opts.userId ? "intro_other" : "intro_self")}</p>`;
    const idle = () => { box.innerHTML = intro + `<div class="card-actions" style="justify-content:flex-start"><button class="btn primary sm" data-q="issue">${T("show")}</button><button class="btn ghost sm" data-q="revoke">${T("revoke_link")}</button></div><p class="small" data-q="msg"></p>`; bind(); };
    const show = q => {
      cur = q;
      box.innerHTML = intro + `<div class="qrbox"><img src="${q.qr_png}" alt="${T("alt")}: ${esc(q.user)}" width="220" height="220">` +
        `<div><p class="small">${esc(q.user)} · ${T("until")} ${until(q.expires)}</p><p class="small mono qrlink">${esc(q.link)}</p>` +
        `<div class="field"><label class="label" for="qr_mail">${T("mail")}</label><input id="qr_mail" class="input" type="email" autocomplete="email" value="${esc(q.email || opts.email || "")}"></div>` +
        `<div class="card-actions" style="justify-content:flex-start"><button class="btn primary sm" data-q="mail">${T("send")}</button>` +
        `<a class="btn ghost sm" download="qr-vhod.png" href="${q.qr_png}">${T("png")}</a><button class="btn ghost sm" data-q="copy">${T("copy")}</button>` +
        `<button class="btn ghost sm" data-q="issue">${T("refresh")}</button><button class="btn ghost sm" data-q="revoke">${T("revoke")}</button></div><p class="small" data-q="msg"></p></div></div>`;
      bind();
    };
    const msg = t => { const m = box.querySelector('[data-q="msg"]'); if (m) m.textContent = t; };
    function bind() {
      box.querySelectorAll("button[data-q]").forEach(b => b.onclick = async () => {
        try {
          if (b.dataset.q === "issue") show(await call("/api/qr/issue", who));
          else if (b.dataset.q === "revoke") { await call("/api/qr/revoke", who); cur = null; idle(); msg(T("revoked")); }
          else if (b.dataset.q === "copy") { await navigator.clipboard.writeText(cur.link); msg(T("copied")); }
          else if (b.dataset.q === "mail") {
            const r = await call("/api/qr/email", {...who, email: box.querySelector("#qr_mail").value.trim()});
            show(r);
            if (r.sent) msg(T("sent", r.email));
            else { msg(T("no_smtp")); location.href = r.mailto; }
          }
        } catch (e) { msg(e.message === "no e-mail in the profile" ? T("need_mail") : e.message === "e-mail" ? T("bad_mail") : e.message); }
      });
    }
    idle();
  }
  window.QRLink = {mount, t: async (k, lang) => { await dict(); L = D[lang] ? lang : "ru"; return T(k); }};
})();
