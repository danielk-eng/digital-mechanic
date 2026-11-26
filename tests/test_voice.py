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
