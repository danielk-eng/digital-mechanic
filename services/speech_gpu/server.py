"""Local GPU speech service (same API as services/speech): nothing leaves the host.

POST /stt  (multipart: audio, lang, prompt) -> {"text", "ms", "model"}
POST /tts  (json: {"text", "speaker"?})     -> audio/wav
GET  /health                                -> {"ready", "models", ...}

Engines (all licences allow commercial use, docs/LOCAL_GPU.md):
  ru      GigaAM-v3 e2e_rnnt (MIT): punctuated, normalised text
  kk, uz  GigaAM-Multilingual-v2 rnnt (MIT), language forced
  others  faster-whisper large-v3-turbo (MIT), e.g. hi, vi; also the fallback for audio > GIGAAM_MAX_S
TTS: Silero v5_cis_base_nostress (MIT; the v4/v5 *_ru models are CC BY-NC and are not used here).
"""
import ru_tts
import io, os, subprocess, tempfile, threading, time, wave

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

ROOT = os.getenv("MODELS_DIR", "/models")
GIGAAM_DEVICE = os.getenv("GIGAAM_DEVICE", "cuda")
GIGAAM_MAX_S = float(os.getenv("GIGAAM_MAX_S", "25"))  # GigaAM short-form limit; longer audio goes to whisper
RU_MODEL = os.getenv("GIGAAM_RU", "ai-sage/GigaAM-v3@e2e_rnnt")
ML_MODEL = os.getenv("GIGAAM_ML", "ai-sage/GigaAM-Multilingual-v2@rnnt")
ML_LANGS = set(os.getenv("GIGAAM_ML_LANGS", "kk,uz,ky,tg,be,az,hy,ka,tk,tt,ba").split(","))
WHISPER = os.getenv("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")
TTS_ON = os.getenv("TTS_ENABLED", "1") == "1"
TTS_MODEL = os.getenv("TTS_MODEL", "v5_cis_base_nostress")
TTS_SPEAKER = os.getenv("TTS_SPEAKER", "ru_zhadyra")
os.environ.setdefault("HF_HOME", f"{ROOT}/hf")

app = FastAPI(title="speech-gpu")
_m: dict = {}
_lock = threading.Lock()  # one GPU job at a time: the LLM shares the card
_ready = threading.Event()


def _gigaam(spec: str):
    if spec not in _m:
        import torch
        from transformers import AutoModel
        repo, rev = spec.split("@")
        if "Multilingual" in repo:  # the v2 loader places itself on the device (model card)
            m = AutoModel.from_pretrained(repo, revision=rev, trust_remote_code=True, language="auto", device=GIGAAM_DEVICE,
                                          fp16_encoder=GIGAAM_DEVICE != "cpu")
        else:
            m = AutoModel.from_pretrained(repo, revision=rev, trust_remote_code=True).to(GIGAAM_DEVICE)
        _m[spec] = m.eval()
        torch.set_grad_enabled(False)
    return _m[spec]


def _whisper():
    if "whisper" not in _m:
        from faster_whisper import WhisperModel
        _m["whisper"] = WhisperModel(WHISPER, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE, download_root=f"{ROOT}/whisper")
    return _m["whisper"]


def _tts():
    if "tts" not in _m:
        import torch
        torch.hub.set_dir(f"{ROOT}/torch")
        model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker=TTS_MODEL, trust_repo=True)
        _m["tts"] = model
    return _m["tts"]


def _text(r) -> str:
    """GigaAM returns a str (v3) or a dict / object with raw and e2e heads (Multilingual-v2): prefer the formatted one."""
    if isinstance(r, str):
        return r
    if isinstance(r, dict):
        for k in ("e2e", "text", "raw"):
            v = r.get(k)
            if v is not None:
                return v if isinstance(v, str) else getattr(v, "text", str(v))
    return getattr(r, "text", str(r))


@app.on_event("startup")
def warm():
    def go():
        try:
            with _lock:
                _gigaam(RU_MODEL)
                _gigaam(ML_MODEL)
                _whisper()
                if TTS_ON:
                    _tts()
        finally:
            _ready.set()
    threading.Thread(target=go, daemon=True).start()


@app.get("/health")
def health():
    return {"ready": _ready.is_set(), "models": sorted(_m), "ru": RU_MODEL, "multilingual": ML_MODEL, "whisper": WHISPER,
            "tts": TTS_ON and "tts" in _m}


def _to_wav16k(data: bytes, path: str) -> float:
    with tempfile.NamedTemporaryFile(suffix=".in") as src:
        src.write(data); src.flush()
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src.name, "-ac", "1", "-ar", "16000", path], check=True)
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


@app.post("/stt")
async def stt(audio: UploadFile = File(...), lang: str = Form("ru"), prompt: str = Form("")):
    data = await audio.read()
    lang = (lang or "ru").lower()
    t0 = time.time()
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "a.wav")
        try:
            dur = _to_wav16k(data, wav)
        except subprocess.CalledProcessError:
            raise HTTPException(400, "bad audio")
        with _lock:
            if lang == "ru" and dur <= GIGAAM_MAX_S:
                name, text = RU_MODEL, _text(_gigaam(RU_MODEL).transcribe(wav))
            elif lang in ML_LANGS and dur <= GIGAAM_MAX_S:
                name, text = ML_MODEL, _text(_gigaam(ML_MODEL).transcribe(wav, language=lang))
            else:
                segs, _ = _whisper().transcribe(wav, language=lang or None, initial_prompt=prompt or None, vad_filter=True,
                                                beam_size=1, condition_on_previous_text=False)
                name, text = WHISPER, " ".join(s.text.strip() for s in segs)
    return {"text": text.strip(), "ms": int((time.time() - t0) * 1000), "model": name.split("/")[-1], "duration": round(dur, 2)}


class TtsIn(BaseModel):
    text: str
    speaker: str = ""


@app.post("/tts")
def tts(body: TtsIn):
    if not TTS_ON:
        raise HTTPException(404, "tts disabled")
    sp = body.speaker if body.speaker.startswith(("ru_", "kaz_", "uzb_")) else TTS_SPEAKER
    with _lock:
        text = ru_tts.prepare(body.text[:900]) if sp.startswith("ru_") else body.text[:900]  # digits + stress for Russian
        audio = _tts().apply_tts(text=text, speaker=sp, sample_rate=24000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes((audio.clamp(-1, 1) * 32767).short().numpy().tobytes())
    return Response(buf.getvalue(), media_type="audio/wav")
