"""Speech: Deepgram STT (cloud, nova-3) with local faster-whisper fallback; Silero TTS (local, optional).

Audio is not stored; only the masked transcript goes to the agent. Voice control in the UI also
works in the browser (Web Speech API) when the server has no STT configured.
"""

from __future__ import annotations

import io
import tempfile
import threading

import httpx

from .config import Settings

_whisper = None
_whisper_lock = threading.Lock()
_silero = None


class SttUnavailable(Exception):
    pass


def deepgram_stt(audio: bytes, mime: str, lang: str, s: Settings) -> str:
    if not s.deepgram_api_key:
        raise SttUnavailable("no deepgram key")
    params = {"model": s.deepgram_model, "language": lang, "smart_format": "true", "punctuate": "true",
              "mip_opt_out": "true"}  # do not let the provider keep audio for model training
    r = httpx.post("https://api.deepgram.com/v1/listen", params=params, content=audio,
                   headers={"Authorization": f"Token {s.deepgram_api_key}", "Content-Type": mime or "audio/webm"}, timeout=30)
    if r.status_code != 200:
        raise SttUnavailable(f"deepgram {r.status_code}")
    alts = r.json()["results"]["channels"][0]["alternatives"]
    return (alts[0].get("transcript") or "").strip() if alts else ""


def whisper_stt(audio: bytes, lang: str, s: Settings) -> str:
    global _whisper
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        raise SttUnavailable("faster-whisper not installed") from None
    with _whisper_lock:
        if _whisper is None:
            _whisper = WhisperModel(s.whisper_model, device="cpu", compute_type="int8")
        with tempfile.NamedTemporaryFile(suffix=".audio") as f:
            f.write(audio); f.flush()
            segs, _ = _whisper.transcribe(f.name, language=lang, vad_filter=True)
            return " ".join(seg.text.strip() for seg in segs).strip()


def transcribe(audio: bytes, mime: str, lang: str, s: Settings) -> tuple[str, str]:
    """Return (text, engine). Deepgram first, local whisper as fallback."""
    errors = []
    for name, fn in (("deepgram", lambda: deepgram_stt(audio, mime, lang, s)), ("whisper", lambda: whisper_stt(audio, lang, s))):
        try:
            return fn(), name
        except SttUnavailable as e:
            errors.append(str(e))
        except Exception as e:  # network etc.
            errors.append(type(e).__name__)
    raise SttUnavailable("; ".join(errors))


def silero_tts(text: str, s: Settings) -> bytes:
    """Russian TTS with Silero v4 (CPU). Returns WAV bytes."""
    global _silero
    if not s.silero_enabled:
        raise SttUnavailable("silero disabled")
    import torch  # type: ignore

    if _silero is None:
        _silero, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru", trust_repo=True)
    import wave

    audio = _silero.apply_tts(text=text[:900], speaker="xenia", sample_rate=24000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes((audio.clamp(-1, 1) * 32767).short().numpy().tobytes())
    return buf.getvalue()
