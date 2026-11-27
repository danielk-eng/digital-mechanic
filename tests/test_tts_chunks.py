"""Server voice: long, number-heavy answers are spoken sentence by sentence (Silero failed on the whole text -> 404 ->
the client switched the whole session to the browser voice); unspeakable bits are never sent alone."""
import io
import wave
from types import SimpleNamespace

from digital_mechanic import voice

LONG = "Момент затяжки 180–200 Н·м (18–20 кгс·м), зазор 0,25–0,30 мм, давление 400–700 кПа на с. 268 и 276. " * 8


def _wav(n=240):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\0\0" * n)
    return b.getvalue()


def test_chunks_short_and_cover_text():
    cs = voice.tts_chunks(LONG)
    assert len(cs) > 3 and all(len(c) <= voice.TTS_CHUNK for c in cs)
    assert "".join(cs).replace(" ", "") == LONG.replace(" ", "")[:len("".join(cs).replace(" ", ""))]
    assert voice.tts_chunks("—") == [] and voice.tts_chunks("") == []
    assert voice.tts_chunks("ТНВД. A. Проверь насос.") == ["ТНВД. A. Проверь насос."]


def test_silero_tts_joins_chunks(monkeypatch):
    sent = []

    class R:
        def __init__(self, ok): self.status_code, self.content = (200, _wav()) if ok else (500, b"")

    def post(url, json, timeout):
        sent.append(json["text"]); return R(len(json["text"]) <= voice.TTS_CHUNK)
    monkeypatch.setattr(voice.httpx, "post", post)
    s = SimpleNamespace(speech_url="http://speech:9000", silero_enabled=False)
    wav = voice.silero_tts(LONG, s)
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() == 240 * len(sent)
    assert len(sent) == len(voice.tts_chunks(LONG))


def test_client_keeps_server_voice_after_one_failure():
    js = open("src/digital_mechanic/web/static/app.js", encoding="utf-8").read()
    assert "if (r.status === 404) SERVER_TTS = false;" in js
