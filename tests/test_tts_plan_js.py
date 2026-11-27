"""Play on a translated repair must speak the shown translation with a matching voice.
A missing browser voice (uz/tg/kk/ky/hi/bn/vi on a typical Linux Chrome) must not fall back to the
default voice reading Cyrillic or Latin as gibberish: speak the Russian original with ru-RU, or stay silent."""
import shutil
import subprocess
from pathlib import Path

import pytest

TTS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/tts.js"
APP = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"
HTML = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/index.html"

VOICES = [
    {"name": "Google русский", "lang": "ru-RU"},
    {"name": "Google US English", "lang": "en-US"},
    {"name": "Google हिन्दी", "lang": "hi-IN"},
    {"name": "Microsoft HoaiMy Online", "lang": "vi-VN"},
]


def _run(expr: str, voices=None) -> str:
    import json
    v = json.dumps(VOICES if voices is None else voices, ensure_ascii=False)
    js = TTS.read_text(encoding="utf-8")
    out = subprocess.run(
        ["node", "-e", js + f"; const V = {v}; process.stdout.write(JSON.stringify({expr}))"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    if out.returncode:
        raise AssertionError(out.stderr or out.stdout)
    return out.stdout


def _plan(shown, original, lang, voices=None):
    import json
    expr = f"ttsPlan({json.dumps(shown, ensure_ascii=False)}, {json.dumps(original, ensure_ascii=False)}, {json.dumps(lang)}, V)"
    return json.loads(_run(expr, voices))


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_russian_with_ru_voice():
    p = _plan("Ремонты этой машины: TKT-DEMO-103, Сцепление, Сидоров П.", None, "ru")
    assert p["text"].startswith("Ремонты этой машины")
    assert p["lang"] == "ru-RU" and p["voice"]["lang"] == "ru-RU"
    assert p["speak"] and p["useServer"] and not p["noticeKey"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_no_translation_speaks_russian_even_if_ui_is_uz():
    ru = "Ремонты этой машины: 30.10.2026 — TKT-DEMO-103, Сцепление, Сидоров П."
    p = _plan(ru, ru, "uz")
    assert p["text"] == ru and p["lang"] == "ru-RU" and p["voice"]["lang"] == "ru-RU"
    assert p["speak"] and not p["fallback"] and not p["noticeKey"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_translated_uz_without_uz_voice_falls_back_to_russian_with_notice():
    ru = "Сцепление, Сидоров П."
    uz = "Mufta, Sidorov P."
    p = _plan(uz, ru, "uz")
    assert p["text"] == ru and p["lang"] == "ru-RU" and p["voice"]["lang"] == "ru-RU"
    assert p["fallback"] and p["noticeKey"] == "tts_no_voice" and p["noticeLang"] == "Oʻzbekcha"
    assert p["speak"] and p["useServer"]
    # must not hand the Latin text to the English default voice
    assert p["voice"]["lang"].startswith("ru")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_translated_uz_with_uz_voice_speaks_shown_text():
    ru = "Сцепление, Сидоров П."
    uz = "Mufta, Sidorov P."
    voices = VOICES + [{"name": "uzbek", "lang": "uz-UZ"}]
    p = _plan(uz, ru, "uz", voices)
    assert p["text"] == uz and p["lang"] in ("uz-UZ", "uz") and p["voice"]["lang"].startswith("uz")
    assert p["speak"] and not p["fallback"] and not p["noticeKey"] and not p["useServer"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_missing_voice_for_ky_kk_tg_falls_back_to_russian():
    ru = "Замена сцепления выполнена."
    shown = {
        "ky": "Муфта алмаштырылды.",
        "kk": "Ілініс ауыстырылды.",
        "tg": "Муфта иваз карда шуд.",
    }
    for lang, tr in shown.items():
        p = _plan(tr, ru, lang)
        assert p["text"] == ru, lang
        assert p["lang"] == "ru-RU" and p["noticeKey"] == "tts_no_voice" and p["fallback"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_hi_and_vi_use_native_voice_when_installed():
    p = _plan("क्लच बदल दिया गया।", "Сцепление заменено.", "hi")
    assert p["text"].startswith("क्लच") and p["voice"]["lang"].startswith("hi") and not p["fallback"]
    p = _plan("Đã thay bộ ly hợp.", "Сцепление заменено.", "vi")
    assert p["text"].startswith("Đã thay") and p["voice"]["lang"].startswith("vi") and not p["fallback"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_target_language_text_without_original_and_without_voice_is_not_spoken():
    p = _plan("Mufta, Sidorov P.", "Mufta, Sidorov P.", "uz")
    assert p["speak"] is False and p["noticeKey"] == "tts_no_voice_only"
    assert p["voice"] is None and p["text"].startswith("Mufta")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_only_english_voices_do_not_read_russian():
    p = _plan("Ремонты этой машины.", "Ремонты этой машины.", "ru", [{"name": "en", "lang": "en-US"}])
    assert p["speak"] is False and p["noticeKey"] == "tts_no_voice_ru" and p["useServer"]
    assert p["voice"] is None and p["lang"] == "ru-RU"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_pick_voice_accepts_underscore_and_prefers_exact_bcp47():
    import json
    voices = [{"name": "a", "lang": "ru"}, {"name": "b", "lang": "ru_RU"}, {"name": "c", "lang": "en-US"}]
    v = json.loads(_run("pickVoice(V, 'ru')", voices))
    assert v["lang"] in ("ru_RU", "ru-RU", "ru") and v["name"] in ("a", "b")
    assert json.loads(_run("voiceMatches({lang:'kk-KZ'}, 'kk')")) is True
    assert json.loads(_run("voiceMatches({lang:'en-US'}, 'uz')")) is False


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_hint_lang_cyrillic_targets_and_latin_uz():
    import json
    assert json.loads(_run("hintLang('Ілініс ауыстырылды ә', 'kk')")) == "kk"
    assert json.loads(_run("hintLang('Ремонты этой машины', 'uz')")) == "ru"
    assert json.loads(_run("hintLang('Mufta almashtirildi', 'uz')")) == "uz"


def test_player_uses_plan_and_does_not_keep_old_cross_lang_fallback():
    js = APP.read_text(encoding="utf-8")
    assert "ttsPlan(" in js and "ttsVoices" in js
    assert "const vv = ttsVoice(ALANG())" not in js
    assert 'uz: ["uz", "tr"]' not in js  # Turkish voice must not read Uzbek
    html = HTML.read_text(encoding="utf-8")
    assert html.index("/static/tts.js") < html.index("/static/app.js")


def test_speech_lang_tables_match():
    import re
    app = APP.read_text(encoding="utf-8")
    tts = TTS.read_text(encoding="utf-8")
    def table(src, name):
        return dict(re.findall(r'(\w+): "([^"]+)"', re.search(rf"const {name} = \{{([^}}]*)\}}", src).group(1)))
    assert table(app, "SPEECH_LANG") == table(tts, "SPEECH_LANG")
    assert table(app, "LANG_NAMES") == table(tts, "LANG_NAMES")
