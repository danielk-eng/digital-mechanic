// Accessibility controls shared by mechanic and manager pages: text size (A / A+ / A++) and voice-over toggle.
// Both settings live in localStorage, so they survive reloads and apply before the page paints.
(function () {
  const SIZES = ["n", "l", "xl"];
  const root = document.documentElement;
  const getSize = () => (SIZES.includes(localStorage.getItem("fs")) ? localStorage.getItem("fs") : "n");
  root.dataset.fs = getSize();
  const THEMES = ["dark", "contrast", "light"], THEME_ICON = {dark: "🌙", contrast: "◐", light: "☀️"};
  const getTheme = () => (THEMES.includes(localStorage.getItem("theme")) ? localStorage.getItem("theme") : "dark");
  root.dataset.theme = getTheme();
  window.A11Y = {
    size: getSize,
    setSize(s) { if (!SIZES.includes(s)) return; localStorage.setItem("fs", s); root.dataset.fs = s; mark(); },
    theme: getTheme,
    setTheme(th) { if (!THEMES.includes(th)) return; localStorage.setItem("theme", th); root.dataset.theme = th; mark(); },
    ttsOn() { return localStorage.getItem("tts") === "1"; },
    setTts(on) { localStorage.setItem("tts", on ? "1" : "0"); if (!on) A11Y.stop(); mark(); },
    audio: null,
    stop() {
      try { if (A11Y.audio) { A11Y.audio.pause(); A11Y.audio = null; } } catch (_) {}
      try { if (window.speechSynthesis) speechSynthesis.cancel(); } catch (_) {}
    },
  };
  function mark() {
    document.querySelectorAll("[data-fs-set]").forEach(b => b.classList.toggle("on", b.dataset.fsSet === getSize()));
    document.querySelectorAll("[data-theme-toggle]").forEach(b => { b.textContent = THEME_ICON[getTheme()]; b.title = getTheme(); });
    document.querySelectorAll("[data-tts-toggle]").forEach(b => { b.textContent = A11Y.ttsOn() ? "🔊" : "🔇"; b.classList.toggle("on", A11Y.ttsOn()); b.setAttribute("aria-pressed", A11Y.ttsOn()); });
  }
  function mount() {
    const header = document.querySelector("header");
    if (!header || header.querySelector(".a11y")) return;
    const box = document.createElement("div");
    box.className = "a11y"; box.setAttribute("role", "group"); box.setAttribute("aria-label", "text size");
    box.innerHTML = `<button data-fs-set="n" title="A">A</button><button data-fs-set="l" title="A+">A+</button><button data-fs-set="xl" title="A++">A++</button><button data-theme-toggle title="theme">🌙</button>` +
      (header.dataset.tts !== "off" ? `<button data-tts-toggle title="voice">🔇</button>` : "");
    const anchor = header.querySelector(".grow");
    anchor ? anchor.after(box) : header.appendChild(box);
    box.querySelectorAll("[data-fs-set]").forEach(b => b.onclick = () => A11Y.setSize(b.dataset.fsSet));
    box.querySelectorAll("[data-tts-toggle]").forEach(b => b.onclick = () => A11Y.setTts(!A11Y.ttsOn()));
    box.querySelectorAll("[data-theme-toggle]").forEach(b => b.onclick = () => A11Y.setTheme(THEMES[(THEMES.indexOf(getTheme()) + 1) % THEMES.length]));
    mark();
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", mount) : mount();
})();
