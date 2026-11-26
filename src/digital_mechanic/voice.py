"""Speech. STT order: local speech service (faster-whisper large-v3-turbo, audio stays on the stand) ->
Deepgram nova-3 (cloud, ru/hi/vi, mip_opt_out) -> in-process faster-whisper -> browser Web Speech (client side).
TTS: local speech service (Silero, ru) -> in-process Silero -> browser speech (client side).

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
            segs, _ = _whisper.transcribe(f.name, language=lang, vad_filter=True, initial_prompt=DOMAIN_PROMPT)
            return " ".join(seg.text.strip() for seg in segs).strip()


DEEPGRAM_LANGS = {"ru", "hi", "vi"}
# Domain vocabulary for Whisper (initial_prompt): brands, units, typical complaint words.
DOMAIN_PROMPT = ("Диагностика грузовика. КамАЗ, МАЗ, Урал, ПАЗ, ГАЗ, УАЗ, ЯМЗ-236, ЯМЗ-238, ЯМЗ-536, ЗМЗ-409, Cummins. "
                 "Стук клапанов, гидрокомпенсатор, форсунка, ТНВД, турбокомпрессор, давление масла, антифриз, "
                 "свеча накала, распредвал, ГРМ, код F01, заявка ТОиР, Н·м, бар, об/мин.")


def speech_stt(audio: bytes, mime: str, lang: str, s: Settings) -> str:
    if not s.speech_url:
        raise SttUnavailable("no speech service")
    r = httpx.post(f"{s.speech_url.rstrip('/')}/stt", files={"audio": ("a", audio, mime or "audio/webm")},
                   data={"lang": lang, "prompt": DOMAIN_PROMPT}, timeout=60)
    if r.status_code != 200:
        raise SttUnavailable(f"speech {r.status_code}")
    return r.json()["text"].strip()


def transcribe(audio: bytes, mime: str, lang: str, s: Settings) -> tuple[str, str]:
    """Return (text, engine): local whisper service -> Deepgram (ru/hi/vi) -> in-process whisper."""
    errors = []
    chain = [("whisper-local", lambda: speech_stt(audio, mime, lang, s))]
    if lang in DEEPGRAM_LANGS:
        chain.append(("deepgram", lambda: deepgram_stt(audio, mime, lang, s)))
    chain.append(("whisper", lambda: whisper_stt(audio, lang, s)))
    for name, fn in chain:
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
    if s.speech_url:
        try:
            r = httpx.post(f"{s.speech_url.rstrip('/')}/tts", json={"text": text[:900]}, timeout=60)
            if r.status_code == 200:
                return r.content
        except httpx.HTTPError:
            pass
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
