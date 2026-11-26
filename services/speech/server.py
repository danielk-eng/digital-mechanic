"""Local speech service for the stand: faster-whisper STT + Silero TTS (Russian). Audio never leaves the host.

POST /stt  (multipart: audio, lang, prompt)  -> {"text", "ms", "model"}
POST /tts  (json: {"text"})                  -> audio/wav
GET  /health
Models are cached in /models (bind mount), so restarts do not re-download.
"""
import io, os, tempfile, threading, time, wave

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto -> cuda if available, else cpu
COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")
THREADS = int(os.getenv("WHISPER_THREADS", str(os.cpu_count() or 4)))
BEAM = int(os.getenv("WHISPER_BEAM", "1"))  # greedy: ~2x faster on CPU, near-equal quality on short commands
ROOT = os.getenv("MODELS_DIR", "/models")
TTS_ON = os.getenv("SILERO_ENABLED", "1") == "1"

app = FastAPI(title="speech")
_w = None; _wl = threading.Lock(); _t = None; _tl = threading.Lock()


def whisper():
    global _w
    if _w is None:
        from faster_whisper import WhisperModel
        _w = WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE, cpu_threads=THREADS, download_root=f"{ROOT}/whisper")
    return _w


def silero():
    global _t
    if _t is None:
        import torch
        torch.hub.set_dir(f"{ROOT}/torch")
        torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))
        _t, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru", trust_repo=True)
    return _t


@app.on_event("startup")
def warm():
    def go():
        with _wl:
            whisper()
        if TTS_ON:
            with _tl:
                silero()
    threading.Thread(target=go, daemon=True).start()


@app.get("/health")
def health():
    return {"whisper": MODEL, "loaded": _w is not None, "tts": TTS_ON and _t is not None}


@app.post("/stt")
async def stt(audio: UploadFile = File(...), lang: str = Form("ru"), prompt: str = Form("")):
    data = await audio.read()
    t0 = time.time()
    with _wl, tempfile.NamedTemporaryFile(suffix=".audio") as f:  # temp file deleted on close
        f.write(data); f.flush()
        segs, info = whisper().transcribe(f.name, language=lang or None, initial_prompt=prompt or None,
                                          vad_filter=True, beam_size=BEAM, condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segs).strip()
    return {"text": text, "ms": int((time.time() - t0) * 1000), "model": MODEL, "duration": round(info.duration, 2)}


class TtsIn(BaseModel):
    text: str
    speaker: str = "xenia"


@app.post("/tts")
def tts(body: TtsIn):
    if not TTS_ON:
        raise HTTPException(404, "tts disabled")
    with _tl:
        audio = silero().apply_tts(text=body.text[:900], speaker=body.speaker, sample_rate=24000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes((audio.clamp(-1, 1) * 32767).short().numpy().tobytes())
    return Response(buf.getvalue(), media_type="audio/wav")
