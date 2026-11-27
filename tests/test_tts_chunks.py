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
    assert "JSON.stringify({text: p, lang: spokenLang})" in js
    assert "SERVER_TTS_LANGS.includes(spokenLang)" in js


def test_unicode_tts_chunks_are_speakable():
    assert voice.tts_chunks("Đã thay máy phun. Kiểm tra áp suất.") == ["Đã thay máy phun. Kiểm tra áp suất."]
    assert voice.tts_chunks("क्लच बदल दिया गया। दबाव जाँचें।") == ["क्लच बदल दिया गया। दबाव जाँचें।"]


def test_nvidia_magpie_tts_multipart_language_voice_and_rate(monkeypatch):
    sent = {}

    class R:
        status_code = 200
        content = _wav()

    def post(url, headers, files, timeout):
        sent["url"] = url
        sent["auth"] = headers["Authorization"]
        sent["language"] = files["language"][1]
        sent["voice"] = files["voice"][1]
        sent["rate"] = files["sample_rate_hz"][1]
        return R()

    monkeypatch.setattr(voice.httpx, "post", post)
    s = SimpleNamespace(nvidia_api_key="test-key", nvidia_tts_enabled=True,
                        nvidia_tts_chatterbox_function_id="fn-id", nvidia_tts_magpie_function_id="mag-id",
                        nvidia_tts_timeout_s=1)
    assert voice.nvidia_tts("दबाव जाँचें।", "hi", s).startswith(b"RIFF")
    assert sent["url"].startswith("https://mag-id.invocation.api.nvcf.nvidia.com/")
    assert sent["auth"] == "Bearer test-key"
    assert sent["language"] == "hi-IN"
    assert sent["voice"] == "Magpie-Multilingual.HI-IN.Leo"
    assert sent["rate"] == "44100"
    assert voice.nvidia_tts("Kiểm tra áp suất.", "vi", s).startswith(b"RIFF")
    assert sent["language"] == "vi-VN" and sent["voice"] == "Magpie-Multilingual.VI-VN.Long"
    assert "ru" in voice.server_tts_langs(s) and voice.tts_configured(s)


def test_nvidia_chatterbox_uses_grpc_and_wraps_pcm(monkeypatch):
    sent = {}
    pcm = b"\x01\x00" * 240

    class Fut:
        def result(self, timeout):
            sent["timeout"] = timeout
            return SimpleNamespace(audio=pcm)

    class Service:
        def synthesize(self, **kw):
            sent.update(kw)
            return Fut()

    def riva(key, fid):
        sent["key"] = key; sent["fid"] = fid
        return Service(), SimpleNamespace(LINEAR_PCM=1)

    monkeypatch.setattr(voice, "_riva_service", riva)
    s = SimpleNamespace(nvidia_api_key="test-key", nvidia_tts_enabled=True,
                        nvidia_tts_chatterbox_function_id="fn-id", nvidia_tts_magpie_function_id="mag-id",
                        nvidia_tts_timeout_s=7)
    wav = voice.nvidia_tts("Проверь затяжку 100 Н·м.", "ru", s)
    assert sent["fid"] == "fn-id" and sent["key"] == "test-key"
    assert sent["voice_name"] == "Chatterbox-Multilingual.ru-RU.Male"
    assert sent["language_code"] == "ru-RU" and sent["sample_rate_hz"] == 22050 and sent["future"] is True
    assert sent["timeout"] == 7
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == 22050 and w.readframes(w.getnframes()) == pcm


def test_cloudru_realtime_tts_wraps_pcm(monkeypatch):
    import base64
    import json
    import sys
    from types import SimpleNamespace as NS

    sent = {}
    pcm = b"\x01\x00" * 240

    class WS:
        def __init__(self):
            self.messages = iter([
                json.dumps({"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode()}),
                json.dumps({"type": "response.done"}),
            ])
        def send(self, payload):
            sent["payload"] = json.loads(payload)
        def settimeout(self, timeout):
            sent["timeout"] = timeout
        def recv(self):
            return next(self.messages)
        def close(self):
            sent["closed"] = True

    def create_connection(url, header, timeout, suppress_origin):
        sent["url"] = url; sent["header"] = header; sent["open_timeout"] = timeout
        assert suppress_origin is True
        return WS()

    fake = NS(create_connection=create_connection, WebSocketTimeoutException=TimeoutError)
    monkeypatch.setitem(sys.modules, "websocket", fake)
    s = SimpleNamespace(cloudru_api_key="key", cloudru_tts=True, cloudru_base_url="https://foundation-models.api.cloud.ru/v1",
                        cloudru_tts_model="GigaChat/SaluteSpeech",
                        cloudru_tts_voice="Bab-Freespeech", cloudru_tts_timeout_s=2)
    wav = voice.cloudru_tts("Проверка связи.", "ru", s)
    assert wav.startswith(b"RIFF")
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == 24000 and w.readframes(w.getnframes()) == pcm
    assert "realtime?model=GigaChat%2FSaluteSpeech" in sent["url"]
    assert sent["header"] == ["Authorization: Bearer key"]
    assert sent["payload"]["response"]["audio"]["output"]["voice"] == "Bab-Freespeech"
    assert sent["closed"] is True


def test_cloudru_failure_falls_back_to_nvidia(monkeypatch):
    def no_cloudru(text, lang, s):
        raise voice.SttUnavailable("cloudru down")
    monkeypatch.setattr(voice, "cloudru_tts", no_cloudru)
    monkeypatch.setattr(voice, "nvidia_tts", lambda text, lang, s: _wav())
    s = SimpleNamespace(cloudru_api_key="key", cloudru_tts=True, nvidia_api_key="nv", nvidia_tts_enabled=True,
                        speech_url="", silero_enabled=False)
    assert voice.tts_synthesize("Проверка связи.", "ru", s).startswith(b"RIFF")


def test_voice_mode_has_visible_microphone_scale():
    html = open("src/digital_mechanic/web/static/index.html", encoding="utf-8").read()
    js = open("src/digital_mechanic/web/static/app.js", encoding="utf-8").read()
    css = open("src/digital_mechanic/web/static/app.css", encoding="utf-8").read()
    assert 'id="vWave"' in html and "drawVoiceWave" in js and ".vwave" in css
