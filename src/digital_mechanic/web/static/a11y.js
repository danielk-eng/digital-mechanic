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
  };
})();
