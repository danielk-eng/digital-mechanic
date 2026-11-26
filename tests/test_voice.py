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
