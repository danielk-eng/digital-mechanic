// Text size and theme apply before the page paints (localStorage). Shared by the mechanic and manager pages.
(function () {
  const root = document.documentElement;
  const pick = (k, list, def) => (list.includes(localStorage.getItem(k)) ? localStorage.getItem(k) : def);
  const SIZES = ["n", "l", "xl"], THEMES = ["dark", "light", "contrast"];
  root.dataset.fs = pick("fs", SIZES, "n");
  root.dataset.theme = pick("theme", THEMES, "dark");
  window.A11Y = {
    SIZES, THEMES,
    size: () => pick("fs", SIZES, "n"),
    setSize(s) { if (SIZES.includes(s)) { localStorage.setItem("fs", s); root.dataset.fs = s; } },
    theme: () => pick("theme", THEMES, "dark"),
    setTheme(t) { if (THEMES.includes(t)) { localStorage.setItem("theme", t); root.dataset.theme = t; } },
    // spoken answers only in voice mode or on an explicit play tap
    voiceSpeaks: () => localStorage.getItem("vtts") !== "0",
    setVoiceSpeaks(on) { localStorage.setItem("vtts", on ? "1" : "0"); },
    captions: () => localStorage.getItem("cc") !== "0",
    setCaptions(on) { localStorage.setItem("cc", on ? "1" : "0"); },
  };
})();
