"use strict";
// Browser speech recognition (Web Speech API). Fallback when the server STT answers 503 / is unreachable / is not
// configured at all (the Vercel stand has neither a Deepgram key nor faster-whisper). Shared by the mechanic, engineer
// and chief pages. Recognition only starts on a mic/orb tap: typing never triggers audio.
window.WebSTT = {
  cur: null, server: null,
  available() { return !!(window.SpeechRecognition || window.webkitSpeechRecognition); },
  // server STT configured? /api/session says so (server_stt); a 503 or a network error flips it off for this page
  async serverOk() {
    if (this.server === null) {
      try { const r = await fetch("/api/session", {credentials: "same-origin"}); this.server = (await r.json()).server_stt !== false; }
      catch (_) { this.server = true; }
    }
    return this.server;
  },
  serverFailed(e) { return !e || !e.status || e.status === 503 || e.status === 502 || e.status === 504; },
  // one phrase -> Promise<string> ("" when nothing was heard); rejects with err.code: no-browser-stt | not-allowed |
  // language-not-supported | network | service-not-allowed | audio-capture
  listen(lang, {onInterim = null, maxMs = 30000} = {}) {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { const e = new Error("no-browser-stt"); e.code = "no-browser-stt"; return Promise.reject(e); }
    this.abort();
    const r = new SR(); r.lang = lang || "ru-RU"; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
    this.cur = r;
    let fin = "", done = false, tm = 0;
    const p = new Promise((res, rej) => {
      r.onresult = ev => {
        let inter = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) { const x = ev.results[i]; if (x.isFinal) fin += x[0].transcript; else inter += x[0].transcript; }
        onInterim && onInterim((fin + " " + inter).trim());
      };
      r.onerror = ev => {
        if (ev.error === "no-speech" || ev.error === "aborted") return;  // onend resolves with what we have
        if (done) return; done = true; clearTimeout(tm); if (this.cur === r) this.cur = null;
        const e = new Error(ev.error || "stt"); e.code = ev.error || "stt"; rej(e);
      };
      r.onend = () => { clearTimeout(tm); if (this.cur === r) this.cur = null; if (!done) { done = true; res(fin.trim()); } };
    });
    tm = setTimeout(() => { try { r.stop(); } catch (_) {} }, maxMs);
    try { r.start(); } catch (e) { clearTimeout(tm); this.cur = null; e.code = e.code || "start"; return Promise.reject(e); }
    return p;
  },
  stop() { try { this.cur && this.cur.stop(); } catch (_) {} },   // finish now, keep what was heard
  abort() { const r = this.cur; this.cur = null; try { r && r.abort(); } catch (_) {} },
  // human message for an error code; t = the page's translator (falls back to Russian)
  message(e, t) {
    const k = !e ? "v_no_stt" : e.code === "not-allowed" || e.code === "service-not-allowed" || e.code === "audio-capture" ? "v_no_mic"
      : e.code === "language-not-supported" ? "v_lang_unsupported" : e.code === "network" ? "v_stt_network" : "v_no_stt";
    const ru = {v_no_mic: "Нет доступа к микрофону", v_lang_unsupported: "Браузер не распознаёт речь на этом языке. Напишите вопрос текстом",
      v_stt_network: "Распознавание речи в браузере не отвечает (нет сети). Напишите вопрос текстом",
      v_no_stt: "Голосовой ввод недоступен: сервер распознавания отключён, а этот браузер не умеет распознавать речь. Напишите вопрос текстом или откройте в Chrome, Edge или Safari"};
    const s = t ? t(k) : k; return s && s !== k ? s : ru[k];
  },
};
// BCP-47 tag for the UI language (same table as SPEECH_LANG in app.js)
window.SPEECH_LANG_OF = l => ({ru: "ru-RU", uz: "uz-UZ", tg: "tg-TJ", kk: "kk-KZ", hi: "hi-IN", vi: "vi-VN", bn: "bn-IN", ky: "ky-KG"})[l] || "ru-RU";
