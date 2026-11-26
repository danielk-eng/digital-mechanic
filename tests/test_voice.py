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
