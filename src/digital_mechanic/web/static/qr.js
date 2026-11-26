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
  const until = ts => new Date(ts * 1000).toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
  // box: element to render into; opts: {userId, email, name}
  function mount(box, opts = {}) {
    const who = opts.userId ? {user_id: opts.userId} : {};
    let cur = null;
    const intro = `<p class="small muted">QR-код переносит вход на телефон, планшет или в приложение: наведите камеру, откройте ссылку и введите ${opts.userId ? "свой" : "ваш"} PIN. Ссылка действует 24 часа.</p>`;
    const idle = () => { box.innerHTML = intro + `<div class="card-actions" style="justify-content:flex-start"><button class="btn primary sm" data-q="issue">Показать QR-код</button><button class="btn ghost sm" data-q="revoke">Отозвать ссылку</button></div><p class="small" data-q="msg"></p>`; bind(); };
    const show = q => {
      cur = q;
      box.innerHTML = intro + `<div class="qrbox"><img src="${q.qr_png}" alt="QR-код для входа: ${esc(q.user)}" width="220" height="220">` +
        `<div><p class="small">${esc(q.user)} · действует до ${until(q.expires)}</p><p class="small mono qrlink">${esc(q.link)}</p>` +
        `<div class="field"><label class="label" for="qr_mail">Почта</label><input id="qr_mail" class="input" type="email" autocomplete="email" value="${esc(q.email || opts.email || "")}"></div>` +
        `<div class="card-actions" style="justify-content:flex-start"><button class="btn primary sm" data-q="mail">Отправить на почту</button>` +
        `<a class="btn ghost sm" download="qr-vhod.png" href="${q.qr_png}">Скачать PNG</a><button class="btn ghost sm" data-q="copy">Копировать ссылку</button>` +
        `<button class="btn ghost sm" data-q="issue">Обновить</button><button class="btn ghost sm" data-q="revoke">Отозвать</button></div><p class="small" data-q="msg"></p></div></div>`;
      bind();
    };
    const msg = t => { const m = box.querySelector('[data-q="msg"]'); if (m) m.textContent = t; };
    function bind() {
      box.querySelectorAll("button[data-q]").forEach(b => b.onclick = async () => {
        try {
          if (b.dataset.q === "issue") show(await call("/api/qr/issue", who));
          else if (b.dataset.q === "revoke") { await call("/api/qr/revoke", who); cur = null; idle(); msg("Ссылка отозвана: войти по ней больше нельзя."); }
          else if (b.dataset.q === "copy") { await navigator.clipboard.writeText(cur.link); msg("Ссылка скопирована."); }
          else if (b.dataset.q === "mail") {
            const r = await call("/api/qr/email", {...who, email: box.querySelector("#qr_mail").value.trim()});
            show(r);
            if (r.sent) msg(`Письмо отправлено на ${r.email}.`);
            else { msg("Почтовый сервер не настроен: открываю письмо в вашей почте, QR-код можно скачать и приложить."); location.href = r.mailto; }
          }
        } catch (e) { msg(e.message === "no e-mail in the profile" ? "Укажите почту." : e.message === "e-mail" ? "Проверьте адрес почты." : e.message); }
      });
    }
    idle();
  }
  window.QRLink = {mount};
})();
