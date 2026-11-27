"""Local speech service for the stand: faster-whisper STT + Silero TTS (Russian). Audio never leaves the host.

POST /stt  (multipart: audio, lang, prompt)  -> {"text", "ms", "model"}
POST /tts  (json: {"text"})                  -> audio/wav
GET  /health
Models are cached in /models (bind mount), so restarts do not re-download.
"""
import ru_tts
import io, os, tempfile, threading, time, wave

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto -> cuda if available, else cpu
COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")
THREADS = int(os.getenv("WHISPER_THREADS", "4"))  # more threads hurt on a busy host
BEAM = int(os.getenv("WHISPER_BEAM", "1"))  # greedy: ~2x faster on CPU, near-equal quality on short commands
ROOT = os.getenv("MODELS_DIR", "/models")
TTS_ON = os.getenv("SILERO_ENABLED", "1") == "1"
# Per-language fine-tunes (CT2 dirs under /models), e.g. WHISPER_MODEL_UZ=/models/uz/abduqayum-ct2.
LANG_MODELS = {k[len("WHISPER_MODEL_"):].lower(): v for k, v in os.environ.items() if k.startswith("WHISPER_MODEL_") and v}

app = FastAPI(title="speech")
_ws: dict = {}; _wl = threading.Lock(); _t = None; _tl = threading.Lock()


def model_for(lang: str) -> str:
    return LANG_MODELS.get((lang or "").lower(), MODEL)


def whisper(lang: str = ""):
    name = model_for(lang)
    if name not in _ws:
        from faster_whisper import WhisperModel
        _ws[name] = WhisperModel(name, device=DEVICE, compute_type=COMPUTE, cpu_threads=THREADS, download_root=f"{ROOT}/whisper")
    return _ws[name]


def silero():
    global _t
    if _t is None:
        import torch
        torch.hub.set_dir(f"{ROOT}/torch")
        torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))
        # v5_cis_base_nostress: MIT (v4_ru is CC BY-NC-SA, not for commercial use); numbers and stress by ru_tts.prepare
        _t, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker=ru_tts.TTS_MODEL, trust_repo=True)
    return _t


@app.on_event("startup")
def warm():
    def go():
        with _wl:
            whisper()
            for lang in LANG_MODELS:
                whisper(lang)
        if TTS_ON:
            with _tl:
                silero()
    threading.Thread(target=go, daemon=True).start()


@app.get("/health")
def health():
    return {"whisper": MODEL, "lang_models": LANG_MODELS, "loaded": MODEL in _ws, "tts": TTS_ON and _t is not None}


@app.post("/stt")
async def stt(audio: UploadFile = File(...), lang: str = Form("ru"), prompt: str = Form("")):
    data = await audio.read()
    t0 = time.time()
    with _wl, tempfile.NamedTemporaryFile(suffix=".audio") as f:  # temp file deleted on close
        f.write(data); f.flush()
        name = model_for(lang)
        # fine-tunes were trained without a prompt; a prompt makes them echo it
        segs, info = whisper(lang).transcribe(f.name, language=lang or None, initial_prompt=(prompt if name == MODEL else None) or None,
                                          vad_filter=True, beam_size=BEAM, condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segs).strip()
    return {"text": text, "ms": int((time.time() - t0) * 1000), "model": os.path.basename(name), "duration": round(info.duration, 2)}


class TtsIn(BaseModel):
    text: str
    speaker: str = ru_tts.DEFAULT_SPEAKER


@app.post("/tts")
def tts(body: TtsIn):
    if not TTS_ON:
        raise HTTPException(404, "tts disabled")
    with _tl:
        spk = body.speaker if body.speaker.startswith("ru_") else ru_tts.DEFAULT_SPEAKER  # old clients sent «xenia» (v4_ru)
        audio = silero().apply_tts(text=ru_tts.prepare(body.text[:900]), speaker=spk, sample_rate=24000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes((audio.clamp(-1, 1) * 32767).short().numpy().tobytes())
    return Response(buf.getvalue(), media_type="audio/wav")
