"use strict";
// Browser TTS plan: speak the text on screen in its language, with a matching voice.
// If the browser has no voice for that language, do not let it read the text with a random
// default voice (Cyrillic/Latin comes out as gibberish). Fall back to the Russian original
// with a ru-RU voice, or stay silent and tell the mechanic.
(function () {
  const SPEECH_LANG = {ru: "ru-RU", uz: "uz-UZ", tg: "tg-TJ", kk: "kk-KZ", ky: "ky-KG"};
  const LANG_NAMES = {ru: "Русский", uz: "Oʻzbekcha", tg: "Тоҷикӣ", kk: "Қазақша", ky: "Кыргызча"};
  const OWN_LETTERS = {kk: "әғқңөұүһі", ky: "ңөү", tg: "ғӣқӯҳҷ", uz: "ўқғҳʼʻ‘'"};
  const LATIN_LANGS = {uz: 1};

  function langCode(lang) { return String(lang || "ru").toLowerCase().replace(/_/g, "-").split("-")[0] || "ru"; }
  function voiceLang(v) { return String((v && v.lang) || "").toLowerCase().replace(/_/g, "-"); }
  function bcp47(lang) { const c = langCode(lang); return SPEECH_LANG[c] || (c + "-" + c.toUpperCase()); }

  function voiceMatches(voice, lang) {
    if (!voice || !lang) return false;
    const code = langCode(lang), vl = voiceLang(voice);
    return vl === code || vl.startsWith(code + "-");
  }

  function pickVoice(voices, lang) {
    const list = voices || [], code = langCode(lang), want = bcp47(code).toLowerCase();
    return list.find(v => voiceLang(v) === want) ||
      list.find(v => voiceLang(v).startsWith(code + "-")) ||
      list.find(v => voiceLang(v) === code) || null;
  }

  function isRussian(text) {
    const s = String(text || "");
    if (!/[а-яё]{3,}/i.test(s)) return false;
    const allOwn = Object.values(OWN_LETTERS).join("");
    return ![...s.toLowerCase()].some(ch => allOwn.includes(ch));
  }

  // Which language is `text` actually in, given the UI/answer language we expect?
  function hintLang(text, lang) {
    const s = String(text || ""), code = langCode(lang);
    if (code === "ru") return "ru";
    const own = OWN_LETTERS[code] || "";
    if (own && [...s.toLowerCase()].some(ch => own.includes(ch))) return code;
    const hasCyr = /[а-яё]{3,}/i.test(s);
    if (LATIN_LANGS[code] && !hasCyr) return code;
    if (hasCyr && isRussian(s)) return "ru";
    if (!hasCyr && /[a-z\u0900-\u097F\u0980-\u09FF\u0A80-\u0D7F]{3,}/i.test(s)) return code;
    return hasCyr ? "ru" : code;
  }

  function ttsPlan(shown, original, lang, voices) {
    const orig = String(original || "").trim();
    const shownText = String(shown || orig).trim();
    const code = langCode(lang);
    const listed = Array.isArray(voices) && voices.length > 0;
    const targetVoice = pickVoice(voices, code);
    const ruVoice = pickVoice(voices, "ru");
    const name = LANG_NAMES[code] || code;
    let textLang;
    if (code === "ru") textLang = "ru";
    else if (orig && shownText && shownText !== orig) textLang = code;
    else textLang = hintLang(shownText || orig, code);

    const ruText = (textLang === "ru" ? (shownText || orig) : (orig && isRussian(orig) ? orig : ""));

    if (textLang === "ru") {
      const missingRu = listed && !ruVoice;
      return {
        text: shownText || orig,
        lang: "ru-RU",
        voice: ruVoice || null,
        speak: !missingRu,
        useServer: true,
        fallback: false,
        noticeKey: missingRu ? "tts_no_voice_ru" : null,
        noticeLang: LANG_NAMES.ru,
      };
    }

    if (targetVoice && voiceMatches(targetVoice, code)) {
      return {
        text: shownText || orig,
        lang: targetVoice.lang || bcp47(code),
        voice: targetVoice,
        speak: true,
        useServer: false,
        fallback: false,
        noticeKey: null,
        noticeLang: name,
      };
    }

    if (ruText) {
      const missingRu = listed && !ruVoice;
      return {
        text: ruText,
        lang: "ru-RU",
        voice: ruVoice || null,
        speak: !missingRu,
        useServer: true,
        fallback: true,
        noticeKey: "tts_no_voice",
        noticeLang: name,
      };
    }

    return {
      text: shownText || orig,
      lang: bcp47(code),
      voice: null,
      speak: false,
      useServer: false,
      fallback: true,
      noticeKey: "tts_no_voice_only",
      noticeLang: name,
    };
  }

  const API = {SPEECH_LANG, LANG_NAMES, pickVoice, voiceMatches, hintLang, isRussian, ttsPlan, bcp47, langCode};
  if (typeof module !== "undefined" && module.exports) Object.assign(module.exports, API);
  Object.assign(typeof window !== "undefined" ? window : globalThis, API);
  // isRussian is tested via hintLang / ttsPlan
})();
