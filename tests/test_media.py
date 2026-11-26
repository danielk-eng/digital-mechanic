import importlib
import io
import math
import os
import random
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from digital_mechanic import media


def engine_wav(seconds=6.0, tone=45.0, knock=False, sr=16000, amp=0.3):
    """Synthetic diesel: firing tone + harmonics + noise; knock = loud clicks every 0.5 s."""
    rnd = random.Random(1)
    out = []
    for i in range(int(seconds * sr)):
        t = i / sr
        v = amp * (math.sin(2 * math.pi * tone * t) + 0.5 * math.sin(4 * math.pi * tone * t) + 0.25 * math.sin(6 * math.pi * tone * t)) / 1.75
        v += 0.02 * rnd.uniform(-1, 1)
        if knock and (t % 0.5) < 0.03:
            v += 0.6 * math.sin(2 * math.pi * 900 * t)
        out.append(max(-1, min(1, v)))
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(x * 32767)) for x in out))
    return b.getvalue()


def test_sound_features_tone_rpm_and_quality():
    f = media.features(engine_wav(), "ЯМЗ-236")
    assert f["quality"] == "good" and abs(f["main_tone_hz"] - 45) <= 1 and f["rpm_estimate"] == 900  # 45 Hz * 120 / 6 cyl
    k = media.features(engine_wav(knock=True), "ЯМЗ-236")
    assert k["unevenness"] > f["unevenness"] and any("скачет" in n for n in k["notes"])
    short = media.features(engine_wav(seconds=1.0, amp=0.001), "ЯМЗ-236")
    assert short["quality"] == "bad"
    d = media.describe_sound(f)
    assert "не результат ML-диагностики" in d and "900 об/мин" in d
    assert media.meaningful_speech("Продолжение следует...") == "" and media.meaningful_speech("вот тут стучит на холостых")


@pytest.fixture()
def client(tmp_path):
    os.environ.update(DB_PATH=str(tmp_path / "a.db"), LOG_PATH=str(tmp_path / "l.jsonl"), UPLOADS_DIR=str(tmp_path / "up"), LLM_API_KEY="", LLM_API_KEY_FILE="",
                      ML_SERVICE_URL="http://127.0.0.1:9", TOIR_SERVICE_URL="http://127.0.0.1:9")
    import digital_mechanic.web.app as m
    importlib.reload(m)
    m.AGENT.llm.vision = lambda name, prompt, b64, mime: "Подтёк масла под крышкой головки цилиндров, следы на блоке."
    c = TestClient(m.app)
    assert c.post("/api/login/demo", json={"account": "veteran", "pin": "3001", "consent": True}).status_code == 200
    return c, m


JPG = b"\xff\xd8\xff\xe0" + b"0" * 2000


def test_video_keyframes_and_audio_tied_to_vehicle_history(client):
    c, m = client
    r = c.post("/api/media", data={"kind": "video", "vehicle_id": "M333YK47", "text": "течёт масло", "times": "0.5,2.0,3.5"},
               files=[("frames", ("f0.jpg", JPG, "image/jpeg")), ("frames", ("f1.jpg", JPG, "image/jpeg")), ("frames", ("f2.jpg", JPG, "image/jpeg")),
                      ("audio", ("a.wav", engine_wav(), "audio/wav"))])
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["files"]) == 3 and "Кадр 1 (0.5 с)" in j["description"] and "Звук ролика" in j["description"]
    assert j["features"]["rpm_estimate"] == 900 and j["turn"]["text"]
    assert j["turn"]["sources"], "answer must cite the manual"  # prefetch by the visible signs works without an LLM too
    tl = c.get("/api/history/M333YK47").json()
    assert tl["media"][0]["kind"] == "video" and "Подтёк" in tl["media"][0]["description"]
    from digital_mechanic.history import summary_for_agent
    assert "МЕДИА ОТ МЕХАНИКА" in summary_for_agent(m.AGENT.timeline("M333YK47"))  # the agent sees it in later turns


def test_engine_sound_and_photo_and_delete(client):
    c, m = client
    r = c.post("/api/media", data={"kind": "sound", "vehicle_id": "M333YK47"}, files=[("audio", ("e.wav", engine_wav(knock=True), "audio/wav"))]).json()
    assert r["files"][0].endswith(".wav") and "не результат ML-диагностики" in r["description"] and r["turn"]["text"]
    p = c.post("/api/media", data={"kind": "photo", "vehicle_id": "M333YK47", "text": "приборная панель"},
               files=[("frames", ("d.jpg", JPG, "image/jpeg"))]).json()
    assert p["kind"] == "photo" and p["turn"]["text"]
    assert c.post("/api/media", data={"kind": "sound", "vehicle_id": "M333YK47"}, files=[("audio", ("x.wav", b"not a wav", "audio/wav"))]).json()["features"]["quality"] == "bad"
    assert c.post("/api/media", data={"kind": "gif", "vehicle_id": "M333YK47"}).status_code == 400
    up = m.Path(m.S.uploads_dir)
    assert any(up.iterdir())
    c.delete("/api/me")
    assert not any(up.iterdir()) and not m.STORE.q("SELECT * FROM media")
