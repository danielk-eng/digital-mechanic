// Text size and theme apply before the page paints (localStorage). Shared by the mechanic and manager pages.
(function () {
  const root = document.documentElement;
  const pick = (k, list, def) => (list.includes(localStorage.getItem(k)) ? localStorage.getItem(k) : def);
  const SIZES = ["n", "l", "xl"], THEMES = ["dark", "light", "contrast"];
  root.dataset.fs = pick("fs", SIZES, "n");
  root.dataset.theme = pick("theme", THEMES, "dark");
  // status bar / browser chrome colour follows the theme (installed app looks native)
  const BAR = {dark: "#0e1013", light: "#f5f6f8", contrast: "#000000"};
  function syncBar() {
    let m = document.querySelector('meta[name="theme-color"]');
    if (!m) { m = document.createElement("meta"); m.name = "theme-color"; document.head.appendChild(m); }
    m.content = BAR[root.dataset.theme] || BAR.dark;
  }
  syncBar();
  window.A11Y = {
    SIZES, THEMES,
    size: () => pick("fs", SIZES, "n"),
    setSize(s) { if (SIZES.includes(s)) { localStorage.setItem("fs", s); root.dataset.fs = s; } },
    theme: () => pick("theme", THEMES, "dark"),
    setTheme(t) { if (THEMES.includes(t)) { localStorage.setItem("theme", t); root.dataset.theme = t; syncBar(); } },
    // spoken answers only in voice mode or on an explicit play tap
    voiceSpeaks: () => localStorage.getItem("vtts") !== "0",
    setVoiceSpeaks(on) { localStorage.setItem("vtts", on ? "1" : "0"); },
    captions: () => localStorage.getItem("cc") !== "0",
    setCaptions(on) { localStorage.setItem("cc", on ? "1" : "0"); },
    // soft click on taps (WebAudio, no files); on by default, switched off in the profile / header
    sounds: () => localStorage.getItem("snd") !== "0",
    setSounds(on) { localStorage.setItem("snd", on ? "1" : "0"); },
    sound: (kind) => Snd.play(kind),
  };

  // ---------------------------------------------------------------- tap sounds
  // A short sine blip with a fast decay (peak gain 0.08); the mic gets two notes: up when it opens, down when it closes.
  // Never while the microphone is live (voice dialog open, a recording running): the blip would end up in the speech
  // recognizer; the «stop» note plays after the recorder has been released.
  const Snd = {
    ctx: null, last: 0,
    ac() {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      if (!this.ctx) { try { this.ctx = new AC({latencyHint: "interactive"}); } catch (_) { return null; } }
      if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
      return this.ctx;
    },
    tone(ctx, t, f0, f1, dur, peak) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine"; o.frequency.setValueAtTime(f0, t); o.frequency.exponentialRampToValueAtTime(f1, t + dur);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(peak, t + 0.006); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + dur + 0.02);
    },
    play(kind = "tap") {
      if (!window.A11Y.sounds() || Snd.micLive()) return false;
      const now = performance.now();
      if (kind === "tap" && now - this.last < 60) return false;  // a double event from one tap
      this.last = now;
      const ctx = this.ac(); if (!ctx) return false;
      const t = ctx.currentTime + 0.005;
      if (kind === "mic_on") { this.tone(ctx, t, 520, 560, 0.07, 0.07); this.tone(ctx, t + 0.075, 780, 820, 0.09, 0.07); }
      else if (kind === "mic_off") { this.tone(ctx, t, 780, 740, 0.07, 0.06); this.tone(ctx, t + 0.075, 520, 480, 0.09, 0.06); }
      else this.tone(ctx, t, 1250, 850, 0.045, 0.08);
      return true;
    },
    micLive() {
      const v = document.getElementById("voice");
      if (v && !v.classList.contains("hidden")) return true;  // mechanic's voice dialog: the mic is open
      return !!document.querySelector(".mic-btn.on");          // chief / engineer recording
    },
  };
  window.Snd = Snd;
  // header switch on the chief / engineer pages (the mechanic has it in the profile sheet)
  function sndBtn() {
    const b = document.getElementById("snd"); if (!b) return;
    const draw = () => { const on = window.A11Y.sounds(); b.setAttribute("aria-pressed", String(on)); b.title = on ? "Звук нажатий: вкл" : "Звук нажатий: выкл";
      if (typeof icon === "function") b.innerHTML = icon(on ? "volume-2" : "volume-x"); };
    b.addEventListener("click", () => { window.A11Y.setSounds(!window.A11Y.sounds()); draw(); });
    draw();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", sndBtn); else sndBtn();
  const MIC = "#mic, #mmic, #emic";
  document.addEventListener("click", e => {
    const el = e.target.closest && e.target.closest('button, .btn, .chip, [role="button"], a.btn, label.switch, summary');
    if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return;
    if (el.matches(MIC)) {
      if (el.classList.contains("on")) setTimeout(() => Snd.play("mic_off"), 250);  // after the recorder stopped
      else Snd.play("mic_on");                                                    // before the mic opens
      return;
    }
    if (el.id === "vClose") { setTimeout(() => Snd.play("mic_off"), 250); return; }
    Snd.play("tap");
  }, true);
})();
