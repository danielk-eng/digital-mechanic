from digital_mechanic import voice
from digital_mechanic.config import Settings


def test_stt_chain_local_first_then_deepgram(monkeypatch):
    calls = []
    def fake_speech(a, m, l, s): calls.append("local"); raise voice.SttUnavailable("down")
    def fake_dg(a, m, l, s): calls.append("dg"); return "стук клапанов"
    def fake_w(a, l, s): calls.append("w"); return "x"
    monkeypatch.setattr(voice, "speech_stt", fake_speech)
    monkeypatch.setattr(voice, "deepgram_stt", fake_dg)
    monkeypatch.setattr(voice, "whisper_stt", fake_w)
    assert voice.transcribe(b"1", "audio/webm", "ru", Settings()) == ("стук клапанов", "deepgram")
    assert calls == ["local", "dg"]
    calls.clear()
    # Tajik: Deepgram has no tg -> skip straight to in-process whisper
    assert voice.transcribe(b"1", "audio/webm", "tg", Settings())[1] == "whisper"
    assert calls == ["local", "w"]


def test_cloud_stt_brand_spelling_fixed():
    from digital_mechanic.voice import fix_terms
    assert fix_terms("Двигатель Я М З 536 перегревается") == "Двигатель ЯМЗ-536 перегревается"
    assert fix_terms("з м з 409, т н в д") == "ЗМЗ-409, ТНВД"
    assert fix_terms("Мазда и язык") == "Мазда и язык"


def test_deepgram_sends_keyterms_for_russian(monkeypatch):
    import httpx
    from digital_mechanic import voice
    from digital_mechanic.config import Settings
    seen = {}

    class R:
        status_code = 200
        def json(self):
            return {"results": {"channels": [{"alternatives": [{"transcript": "Я М З 236 стучит"}]}]}}

    def fake_post(url, params=None, **kw):
        seen["params"] = params
        return R()
    monkeypatch.setattr(httpx, "post", fake_post)
    out = voice.deepgram_stt(b"x", "audio/webm", "ru", Settings(deepgram_api_key="k"))
    assert out == "ЯМЗ-236 стучит" and ("keyterm", "ЯМЗ") in seen["params"] and ("mip_opt_out", "true") in seen["params"]


def test_uz_cyrillic_to_latin_and_hi_loanwords():
    from digital_mechanic.voice import hi_devanagari, postprocess, uz_latin
    assert uz_latin("Мой босими паст, двигател тақиллаяпти") == "Moy bosimi past, dvigatel taqillayapti"
    assert uz_latin("Dvigatel salt yurishda") == "Dvigatel salt yurishda"  # already Latin
    assert uz_latin("Ердаги мой") == "Yerdagi moy"
    assert hi_devanagari("Engine idle पर खट खट कर रहा है.") == "इंजन आइडल पर खटखट कर रहा है."
    assert hi_devanagari("Check engine light") == "Check engine light"  # no Devanagari -> untouched
    assert postprocess("Я М З 536", "ru") == "ЯМЗ-536"


def test_stt_route_per_language(monkeypatch):
    from digital_mechanic import voice
    from digital_mechanic.config import Settings
    used = []
    monkeypatch.setattr(voice, "deepgram_stt", lambda a, m, lang, s: used.append(("dg", lang)) or "dg")
    monkeypatch.setattr(voice, "speech_stt", lambda a, m, lang, s: used.append(("wh", lang)) or "wh")
    cf = Settings(stt_order="cloud-first", deepgram_api_key="k", speech_url="http://x")
    assert voice.transcribe(b"x", "audio/webm", "uz", cf) == ("dg", "deepgram")
    assert voice.transcribe(b"x", "audio/webm", "kk", cf) == ("wh", "whisper-local")  # kk: Whisper with a Kazakh prompt is better
    lf = Settings(stt_order="local-first", deepgram_api_key="k", speech_url="http://x")
    assert voice.transcribe(b"x", "audio/webm", "uz", lf) == ("wh", "whisper-local")  # strict 152-FZ: audio stays local
    assert voice.prompt_for("kk").startswith("Жүк") and "Диагностика" not in voice.prompt_for("uz")


def test_localization_country_forces_local_first_stt(monkeypatch):
    """RU/UZ/KZ require data localization: even with STT_ORDER=cloud-first the local Whisper goes first."""
    from digital_mechanic import privacy_laws, voice
    from digital_mechanic.config import Settings
    monkeypatch.setattr(voice, "deepgram_stt", lambda a, m, lang, s: "dg")
    monkeypatch.setattr(voice, "speech_stt", lambda a, m, lang, s: "wh")
    cf = Settings(stt_order="cloud-first", deepgram_api_key="k", speech_url="http://x")
    for c, engine in (("UZ", "whisper-local"), ("KZ", "whisper-local"), ("RU", "whisper-local"), ("IN", "deepgram"), ("VN", "deepgram")):
        assert voice.transcribe(b"x", "audio/webm", "uz" if c != "IN" else "hi", cf, local_first=privacy_laws.requires_localization(c))[1] == engine, c


def test_stt_configured_flag(monkeypatch):
    """Vercel: no speech service, no cloud key, no faster-whisper -> server_stt false, the browser recognises speech."""
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None)
    assert voice.stt_configured(Settings(speech_url="", deepgram_api_key="", cloudru_api_key="")) is False
    assert voice.stt_configured(Settings(speech_url="", deepgram_api_key="k", cloudru_api_key="")) is True
    assert voice.stt_configured(Settings(speech_url="http://speech:8000", deepgram_api_key="", cloudru_api_key="")) is True


def test_browser_stt_fallback_wired():
    """503 from /api/stt -> Web Speech API on all three pages; clear message when the browser has none; 9 languages."""
    import json
    from pathlib import Path
    st = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static"
    for page in ("index.html", "engineer.html", "manager.html"):
        h = (st / page).read_text(encoding="utf-8")
        assert h.index("/static/webstt.js") < h.index("/static/" + {"index.html": "app.js"}.get(page, page.replace(".html", ".js")))
    w = (st / "webstt.js").read_text(encoding="utf-8")
    assert "webkitSpeechRecognition" in w and "e.status === 503" in w
    app = (st / "app.js").read_text(encoding="utf-8")
    assert "WebSTT.serverFailed(e)" in app and "vListenBrowser(t(\"v_browser_stt\"))" in app and "ss.server_stt" in app
    for js in ("engineer.js", "manager.js"):
        s = (st / js).read_text(encoding="utf-8")
        assert "WebSTT.serverFailed(e)" in s and "micBrowser(b, true)" in s
    i18n = json.loads((st / "i18n.json").read_text(encoding="utf-8"))
    for lang, d in i18n.items():
        for k in ("v_browser_stt", "v_no_stt", "v_lang_unsupported", "v_stt_network"):
            assert d.get(k), (lang, k)


def test_nvidia_stt_multipart_language_and_response(monkeypatch):
    from types import SimpleNamespace
    sent = {}

    class R:
        status_code = 200
        def json(self): return {"text": "Я М З 536 стучит"}

    def post(url, headers, files, data, timeout):
        sent.update(url=url, auth=headers["Authorization"], filename=files["file"][0], mime=files["file"][2],
                    language=data["language"], fmt=data["response_format"])
        return R()

    monkeypatch.setattr(voice.httpx, "post", post)
    s = SimpleNamespace(nvidia_api_key="test-key", nvidia_stt_enabled=True, nvidia_stt_function_id="stt-id", speech_timeout_s=1)
    assert voice.nvidia_stt(b"wav", "audio/wav", "ru", s) == "ЯМЗ-536 стучит"
    assert sent["url"].startswith("https://stt-id.invocation.api.nvcf.nvidia.com/")
    assert sent["auth"] == "Bearer test-key" and sent["filename"] == "voice.wav" and sent["mime"] == "audio/wav"
    assert sent["language"] == "ru-RU" and sent["fmt"] == "json"


def test_voice_recording_is_converted_to_wav_for_server_stt():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js").read_text(encoding="utf-8")
    assert "blob = await toWav(blob, 15)" in js and 'name = "voice.wav"' in js
