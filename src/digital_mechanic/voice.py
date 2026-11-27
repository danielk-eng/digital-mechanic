"""Speech. STT order: local speech service (faster-whisper large-v3-turbo, audio stays on the stand) ->
Deepgram nova-3 (cloud, where enabled, mip_opt_out) -> in-process faster-whisper -> browser Web Speech (client side).
TTS: Cloud.ru SaluteSpeech (ru/kk/uz/ky) -> NVIDIA hosted voices where supported -> local Silero/browser speech.

Audio is not stored; only the masked transcript goes to the agent. Voice control in the UI also
works in the browser (Web Speech API) when the server has no STT configured.
"""

from __future__ import annotations

from . import ru_tts

import base64
import io
import json
import re
import tempfile
import threading
import time
from urllib.parse import quote

import httpx

from .config import Settings
from .i18n import LANGS as PRODUCT_LANGS

_whisper = None
_whisper_lock = threading.Lock()
_silero = None


class SttUnavailable(Exception):
    pass


def deepgram_stt(audio: bytes, mime: str, lang: str, s: Settings) -> str:
    if not s.deepgram_api_key:
        raise SttUnavailable("no deepgram key")
    params = [("model", s.deepgram_model), ("language", lang), ("smart_format", "true"), ("punctuate", "true"),
              ("mip_opt_out", "true")]  # do not let the provider keep audio for model training
    if lang == "ru" and s.deepgram_model.startswith("nova-3"):
        params += [("keyterm", k) for k in KEYTERMS_RU]  # keyterm prompting: brands and workshop words
    r = httpx.post("https://api.deepgram.com/v1/listen", params=params, content=audio,
                   headers={"Authorization": f"Token {s.deepgram_api_key}", "Content-Type": mime or "audio/webm"}, timeout=30)
    if r.status_code != 200:
        raise SttUnavailable(f"deepgram {r.status_code}")
    alts = r.json()["results"]["channels"][0]["alternatives"]
    return postprocess((alts[0].get("transcript") or "").strip(), lang) if alts else ""


KEYTERMS_RU = ["ЯМЗ", "ЗМЗ", "КамАЗ", "МАЗ", "УАЗ", "ПАЗ", "ТНВД", "ГРМ", "ГБЦ", "форсунка", "турбокомпрессор", "гидронатяжитель",
               "стучит", "свистит", "троит", "антифриз", "заявка"]


def fix_terms(text: str) -> str:
    """Cloud STT spells brands letter by letter: «Я М З 536» -> «ЯМЗ-536», «ЗМЗ 409» -> «ЗМЗ-409»."""
    text = re.sub(r"(?i)(?<![а-яё])я\s*м\s*з(?![а-яё])", "ЯМЗ", text)
    text = re.sub(r"(?i)(?<![а-яё])з\s*м\s*з(?![а-яё])", "ЗМЗ", text)
    text = re.sub(r"(?i)(?<![а-яё])т\s*н\s*в\s*д(?![а-яё])", "ТНВД", text)
    return re.sub(r"\b(ЯМЗ|ЗМЗ)[\s-]*(\d{3})", r"\1-\2", text)


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
            segs, _ = _whisper.transcribe(f.name, language=lang, vad_filter=True, initial_prompt=prompt_for(lang) or None)
            return postprocess(" ".join(seg.text.strip() for seg in segs).strip(), lang)


# Deepgram nova-3 languages used here. Measured on the stand (audit/asr2): uz WER 0.00 (2.9 s) vs Whisper 0.86+
# (Cyrillic or prompt echo); kk 0.14 (2.3 s) vs Whisper with a Kazakh prompt 0.00 (11 s) -> kk keeps Whisper first.
DEEPGRAM_LANGS = {"ru", "hi", "vi", "uz", "kk", "bn", "ky"}  # bn/ky: nova-3 monolingual (Deepgram changelog 21.01 and 16.11.2026)
# Whisper has no Kyrgyz: for ky there is no local model, only the cloud one (the KG consent text says so).
NO_WHISPER = {"ky"}
DEEPGRAM_FIRST = {"ru", "hi", "vi", "uz"}  # with STT_ORDER=cloud-first (local-first: Whisper + uz Cyrillic->Latin fix)
# Domain vocabulary for Whisper (initial_prompt): brands, units, typical complaint words.
DOMAIN_PROMPT = ("Диагностика грузовика. КамАЗ, МАЗ, Урал, ПАЗ, ГАЗ, УАЗ, ЯМЗ-236, ЯМЗ-238, ЯМЗ-536, ЗМЗ-409, Cummins. "
                 "Стук клапанов, гидрокомпенсатор, форсунка, ТНВД, турбокомпрессор, давление масла, антифриз, "
                 "свеча накала, распредвал, ГРМ, код F01, заявка ТОиР, Н·м, бар, об/мин.")


# Whisper initial_prompt in the language and script of the answer: a Russian prompt pushed kk/uz output to
# Russian Cyrillic (uz WER 1.14, kk 0.29). Uzbek prompt is brand names only (a longer one was echoed back).
LANG_PROMPTS = {
    "ru": DOMAIN_PROMPT,
    "kk": "Жүк көлігінің диагностикасы. Қозғалтқыш тарсылдайды, май қысымы төмен, бос жүріс, салқындату сұйықтығы, КамАЗ, ЯМЗ-236.",
    "tg": "Ташхиси мошини боркаш. Муҳаррик тақ-тақ мекунад, фишори равған паст, гардиши холӣ, КамАЗ, ЯМЗ-236.",
    "uz": "KamAZ, YaMZ-236, ZMZ-409.",
    "hi": "ट्रक डायग्नोस्टिक। इंजन आइडल पर खटखट, तेल का दबाव कम, कूलेंट, कामाज़।",
    "vi": "Chẩn đoán xe tải. Động cơ bị gõ, áp suất dầu thấp, chạy không tải, nước làm mát.",
    "bn": "ট্রাক ডায়াগনস্টিক। ইঞ্জিনে ঠকঠক শব্দ, তেলের চাপ কম, কুল্যান্ট, কামাজ।",
}


def prompt_for(lang: str) -> str:
    return LANG_PROMPTS.get(lang, "")


_UZ_CYR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k",
           "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
           "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "ʼ", "ь": "", "ы": "i", "э": "e", "ю": "yu", "я": "ya", "ў": "oʻ", "қ": "q", "ғ": "gʻ",
           "ҳ": "h"}


def uz_latin(text: str) -> str:
    """Uzbek in Cyrillic (Whisper habit) -> official Latin alphabet; «е» at a word start -> «ye»."""
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    if cyr < max(3, 0.3 * sum(ch.isalpha() for ch in text)):
        return text
    out, prev = [], " "
    for ch in text:
        lo = ch.lower()
        if lo in _UZ_CYR:
            t = "ye" if lo == "е" and not prev.isalpha() else _UZ_CYR[lo]
            out.append(t.capitalize() if ch != lo and t else t)
        else:
            out.append(ch)
        prev = ch
    return "".join(out)


# Deepgram writes English loanwords of spoken Hindi in Latin («Engine idle पर…»); the manual terms are Devanagari.
HI_LOANWORDS = {"engine": "इंजन", "idle": "आइडल", "oil": "ऑयल", "pressure": "प्रेशर", "coolant": "कूलेंट", "radiator": "रेडिएटर",
                "battery": "बैटरी", "belt": "बेल्ट", "pump": "पंप", "filter": "फ़िल्टर", "sensor": "सेंसर", "valve": "वाल्व",
                "piston": "पिस्टन", "gear": "गियर", "clutch": "क्लच", "brake": "ब्रेक", "turbo": "टर्बो", "fan": "फैन",
                "starter": "स्टार्टर", "alternator": "अल्टरनेटर", "injector": "इंजेक्टर", "diesel": "डीज़ल", "temperature": "तापमान",
                "check": "चेक", "service": "सर्विस", "noise": "आवाज़", "smoke": "धुआँ", "leak": "लीक", "ticket": "टिकट"}


def hi_devanagari(text: str) -> str:
    if not re.search(r"[\u0900-\u097f]", text):
        return text  # a fully English utterance stays English
    text = re.sub(r"[A-Za-z]+", lambda m: HI_LOANWORDS.get(m.group(0).lower(), m.group(0)), text)
    return re.sub(r"(?<![\u0900-\u097f])([\u0900-\u097f]{2,3}) \1(?![\u0900-\u097f])", r"\1\1", text)  # «खट खट» -> «खटखट»


def postprocess(text: str, lang: str) -> str:
    if lang == "ru":
        return fix_terms(text)
    if lang == "uz":
        return uz_latin(text)
    if lang == "hi":
        return hi_devanagari(text)
    return text


CLOUDRU_ASR_MODEL = "GigaChat/GigaASR"
CLOUDRU_STT_SKIP = {"uz"}  # whisper-large-v3 measured worse than the stand's own fine-tune; lifted when SaluteSpeech ASR is enabled for uz


def _lang_csv(value: str, default: str = "ru") -> set[str]:
    return {x.strip().lower().split("-")[0] for x in (value or default).split(",") if x.strip()}


def cloudru_asr_langs(s: Settings) -> set[str]:
    # Direct SaluteSpeech documents ru/kk/uz/ky; Cloud.ru GigaASR currently verifies as Russian-only,
    # so non-ru values are an explicit opt-in after the multilingual access request is active.
    return _lang_csv(getattr(s, "cloudru_asr_langs", "ru")) & {"ru", "kk", "uz", "ky"}


def cloudru_tts_langs(s: Settings) -> set[str]:
    # SaluteSpeech documents ru/kk/uz/ky. Cloud.ru Realtime officially lists ru/en today but already synthesises
    # kk/uz/ky text in smoke probes; the matrix stays explicit because quality/access still need native-speaker validation.
    return _lang_csv(getattr(s, "cloudru_tts_langs", "ru")) & {"ru", "kk", "uz", "ky"}


def cloudru_stt(audio: bytes, mime: str, lang: str, s: Settings) -> str:
    """Cloud.ru Foundation Models (servers in Russia): GigaASR where explicitly enabled, whisper-large-v3 otherwise."""
    if not (s.cloudru_api_key and s.cloudru_stt):
        raise SttUnavailable("no cloudru key")
    ext = {"audio/wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/mp4": "m4a"}.get((mime or "").split(";")[0], "webm")
    r = httpx.post(f"{s.cloudru_base_url.rstrip('/')}/audio/transcriptions", headers={"Authorization": f"Bearer {s.cloudru_api_key}"},
                   files={"file": (f"a.{ext}", audio, mime or "audio/webm")},
                   data={"model": CLOUDRU_ASR_MODEL if lang in cloudru_asr_langs(s) else "openai/whisper-large-v3", "language": lang, "prompt": prompt_for(lang) if lang != "ru" else ""},
                   timeout=s.speech_timeout_s)
    if r.status_code != 200:
        raise SttUnavailable(f"cloudru {r.status_code}")
    return postprocess((r.json().get("text") or "").strip(), lang)


NVIDIA_STT_LANGS = {"ru": "ru-RU"}  # parakeet-tdt-0.6b: 25 European languages; among the app languages Russian is covered


def _nvcf_wait(r: httpx.Response, key: str, timeout_s: float) -> httpx.Response:
    """NVCF returns 202 while a public function is cold/queued; poll the generic status endpoint."""
    deadline = time.monotonic() + min(float(timeout_s), 55.0)
    while r.status_code == 202 and time.monotonic() < deadline:
        reqid = r.headers.get("NVCF-REQID") or r.headers.get("nvcf-reqid")
        if not reqid:
            break
        time.sleep(0.7)
        r = httpx.get(f"https://api.nvcf.nvidia.com/v2/nvcf/pexec/status/{reqid}",
                      headers={"Authorization": f"Bearer {key}"}, timeout=10)
    return r


def nvidia_stt(audio: bytes, mime: str, lang: str, s: Settings) -> str:
    """NVIDIA Parakeet HTTP ASR. The client sends 16 kHz mono WAV; webm/opus stays for the other engines."""
    key = _nvidia_key(s)
    if not key or lang not in NVIDIA_STT_LANGS or not getattr(s, "nvidia_stt_enabled", True):
        raise SttUnavailable("no nvidia stt")
    fid = getattr(s, "nvidia_stt_function_id", "")
    if not fid:
        raise SttUnavailable("no nvidia stt endpoint")
    url = (fid.rstrip("/") if fid.startswith("https://") else f"https://{fid}.invocation.api.nvcf.nvidia.com/v1/audio/transcriptions")
    ext = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/flac": "flac", "audio/ogg": "ogg", "audio/opus": "opus"}.get((mime or "").split(";")[0], "wav")
    try:
        r = httpx.post(url, headers={"Authorization": f"Bearer {key}"},
                       files={"file": (f"voice.{ext}", audio, mime or "audio/wav")},
                       data={"language": NVIDIA_STT_LANGS[lang], "response_format": "json"}, timeout=s.speech_timeout_s)
        r = _nvcf_wait(r, key, s.speech_timeout_s)
    except httpx.HTTPError:
        raise SttUnavailable("nvidia stt network") from None
    if r.status_code != 200:
        raise SttUnavailable(f"nvidia stt {r.status_code}")
    try:
        text = r.json().get("text") or ""
    except ValueError:
        text = r.text
    return postprocess(text.strip(), lang)


def speech_stt(audio: bytes, mime: str, lang: str, s: Settings) -> str:
    if not s.speech_url:
        raise SttUnavailable("no speech service")
    r = httpx.post(f"{s.speech_url.rstrip('/')}/stt", files={"audio": ("a", audio, mime or "audio/webm")},
                   data={"lang": lang, "prompt": prompt_for(lang)}, timeout=s.speech_timeout_s)
    if r.status_code != 200:
        raise SttUnavailable(f"speech {r.status_code}")
    return postprocess(r.json()["text"].strip(), lang)


def stt_configured(s: Settings) -> bool:
    """Any server STT engine at all (speech service, cloud key or in-process faster-whisper); false on the Vercel stand."""
    if s.speech_url or s.deepgram_api_key or (s.cloudru_api_key and s.cloudru_stt) or (_nvidia_key(s) and getattr(s, "nvidia_stt_enabled", True)):
        return True
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def transcribe(audio: bytes, mime: str, lang: str, s: Settings, local_first: bool = False, country: str | None = None) -> tuple[str, str]:
    """Return (text, engine): [Cloud.ru for RU-region users] -> local whisper service -> Deepgram -> in-process whisper.
    Cloud.ru goes first for `cloudru_countries` unless the law of the person's country wants the data in that country
    (local_first and country != RU: KZ, UZ): then the stand's own whisper is first and Cloud.ru second."""
    errors = []
    chain = [] if lang in NO_WHISPER else [("whisper-local", lambda: speech_stt(audio, mime, lang, s))]
    ru_region = {x.strip().upper() for x in s.cloudru_countries.split(",") if x.strip()}
    # uz: Cloud.ru whisper-large-v3 WER 0.96 on audit/asr_uz (navai on the stand 0.17, Deepgram 0.13) -> not used for uz
    if (s.cloudru_api_key and s.cloudru_stt and (lang not in CLOUDRU_STT_SKIP or lang in cloudru_asr_langs(s) - {"ru"})
            and (country or "RU").upper() in ru_region):
        cr = ("cloudru", lambda: cloudru_stt(audio, mime, lang, s))
        chain.insert(0 if (not local_first or (country or "RU").upper() == "RU") else len(chain), cr)
    if lang in NVIDIA_STT_LANGS and not local_first:
        chain.append(("nvidia", lambda: nvidia_stt(audio, mime, lang, s)))
    if lang in DEEPGRAM_LANGS:
        dg = ("deepgram", lambda: deepgram_stt(audio, mime, lang, s))
        first = lang in DEEPGRAM_FIRST and s.stt_order == "cloud-first" and not local_first  # local-first (strict 152-FZ): audio stays on the stand
        chain.insert(0, dg) if first else chain.append(dg)
    if lang not in NO_WHISPER:
        chain.append(("whisper", lambda: whisper_stt(audio, lang, s)))
    for name, fn in chain:
        try:
            return fn(), name
        except SttUnavailable as e:
            errors.append(str(e))
        except Exception as e:  # network etc.
            errors.append(type(e).__name__)
    raise SttUnavailable("; ".join(errors))


TTS_CHUNK = 220  # Silero gives up on ~400+ chars once numbers and units are spelled out («180–200 Н·м» -> words)
TTS_MAX = 1200   # whole message as the client sends it (was cut at 900)


def speakable(text: str) -> bool:
    """Letters in any script or digits are speakable, not only Russian."""
    return any(ch.isalpha() or ch.isdigit() for ch in text)


def tts_chunks(text: str, limit: int = TTS_CHUNK) -> list[str]:
    """Sentence-sized pieces (<= limit chars) for the synthesiser; a long sentence is split at , ; : — or spaces.
    Pieces with nothing speakable (only punctuation, e.g. «—») are glued to a neighbour, never sent alone."""
    text = re.sub(r"\s+", " ", text or "").strip()[:TTS_MAX]
    out: list[str] = []
    for sent in re.findall(r"[^.!?…]+[.!?…]*", text):
        sent = sent.strip()
        while len(sent) > limit:
            cut = max(sent.rfind(sep, 0, limit) for sep in (", ", "; ", ": ", " — ", " "))
            cut = cut if cut > limit // 3 else limit
            out.append(sent[:cut + 1].strip()); sent = sent[cut + 1:].strip()
        if sent:
            out.append(sent)
    merged: list[str] = []
    for c in out:  # pack short neighbours together; keep unspeakable bits attached to real words
        if merged and (len(merged[-1]) + 1 + len(c) <= limit or not speakable(c)):
            merged[-1] += " " + c
        else:
            merged.append(c)
    return [c for c in merged if speakable(c)]


def _wav_join(parts: list[bytes]) -> bytes:
    import wave

    frames, params = [], None
    for b in parts:
        with wave.open(io.BytesIO(b)) as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


# Cloud.ru Realtime TTS (`GigaChat/SaluteSpeech`) is the primary Russian server voice: newer, more natural
# LLM-based SaluteSpeech voices than the local Silero fallback. The websocket returns raw PCM16 and/or base64 PCM deltas.
def _pcm16_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sample_rate); w.writeframes(pcm)
    return buf.getvalue()


def _cloudru_realtime_chunk(text: str, s: Settings) -> bytes:
    import websocket

    key = s.cloudru_api_key
    base = s.cloudru_base_url.rstrip('/').replace('https://', 'wss://').replace('http://', 'ws://')
    model = getattr(s, "cloudru_tts_model", "GigaChat/SaluteSpeech")
    url = f"{base}/realtime?model={quote(model, safe='')}"
    sample_rate = 24000
    payload = {
        "type": "response.create",
        "model": model,
        "response": {
            "output_modalities": ["audio"],
            "audio": {"output": {"format": {"type": "audio/pcm", "rate": sample_rate},
                                 "voice": getattr(s, "cloudru_tts_voice", "Bab-Freespeech"), "speed": 1.0}},
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}],
        },
    }
    chunks: list[bytes] = []
    err = ""
    ws = None
    try:
        ws = websocket.create_connection(url, header=[f"Authorization: Bearer {key}"], timeout=10, suppress_origin=True)
        ws.send(json.dumps(payload, ensure_ascii=False))
        deadline = time.monotonic() + getattr(s, "cloudru_tts_timeout_s", 35.0)
        done = False
        while not done and time.monotonic() < deadline:
            ws.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                msg = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            if isinstance(msg, bytes):
                chunks.append(msg)
                continue
            try:
                data = json.loads(msg)
            except (TypeError, ValueError):
                continue
            typ = data.get("type", "")
            if typ in ("response.audio.delta", "response.output_audio.delta", "audio.delta"):
                b64 = data.get("delta") or data.get("audio")
                if b64:
                    chunks.append(base64.b64decode(b64))
            elif typ == "error":
                err = str((data.get("error") or {}).get("message") or "cloudru realtime error")[:160]
                break
            elif typ in ("response.audio.done", "response.output_audio.done", "response.done"):
                done = True
    except Exception as e:  # websocket/connectivity errors must fall back to NVIDIA/Silero/browser
        raise SttUnavailable(f"cloudru tts {type(e).__name__}") from None
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
    if not chunks:
        raise SttUnavailable(err or "cloudru tts empty")
    raw = b"".join(chunks)
    return raw if raw.startswith(b"RIFF") else _pcm16_wav(raw, sample_rate)


def cloudru_tts(text: str, lang: str, s: Settings) -> bytes:
    """Cloud.ru `GigaChat/SaluteSpeech` over the Realtime websocket.
    Multilingual mode is explicit: ru is verified; kk/uz/ky become available through CLOUDRU_TTS_LANGS after access is confirmed."""
    if lang not in cloudru_tts_langs(s) or not s.cloudru_api_key or not getattr(s, "cloudru_tts", True):
        raise SttUnavailable("no cloudru tts")
    prepared = ru_tts.prepare(text) if lang == "ru" else text
    chunks = tts_chunks(prepared, limit=900)  # realtime accepts conversational text; keep long answers bounded
    if not chunks:
        raise SttUnavailable("nothing to speak")
    parts = [_cloudru_realtime_chunk(c, s) for c in chunks]
    return _wav_join(parts) if len(parts) > 1 else parts[0]


# Hosted NVIDIA Build speech endpoints. Chatterbox is the only NVIDIA-hosted multilingual model with Russian;
# Magpie covers Vietnamese (and other languages), but not Russian. A missing voice parameter means the endpoint default.
NVIDIA_TTS_PROFILES = {
    # Chatterbox is the only NVIDIA voice with Russian, but the hosted function exposes gRPC only.
    "ru": {"provider": "chatterbox", "language": "ru-RU", "voice": "Chatterbox-Multilingual.ru-RU.Male", "rate": 22050},
    # Magpie has no Russian, but covers Hindi and Vietnamese through the documented HTTP endpoint. Explicit voices
    # and the documented 44.1 kHz rate matter: the hosted function rejects a missing/default voice or 24 kHz quickly.
    "hi": {"provider": "magpie", "language": "hi-IN", "voice": "Magpie-Multilingual.HI-IN.Leo", "rate": 44100},
    "vi": {"provider": "magpie", "language": "vi-VN", "voice": "Magpie-Multilingual.VI-VN.Long", "rate": 44100},
}


def _nvidia_key(s: Settings) -> str:
    fn = getattr(s, "nvidia_key", None)
    return fn() if callable(fn) else getattr(s, "nvidia_api_key", "")


def _nvidia_tts_url(function_id: str) -> str:
    if function_id.startswith("https://"):
        return function_id.rstrip("/")
    return f"https://{function_id}.invocation.api.nvcf.nvidia.com/v1/audio/synthesize"


def _riva_service(key: str, function_id: str):
    """Optional Riva gRPC client. Imported lazily so local/browser-only installs need not carry grpcio."""
    try:
        import riva.client  # type: ignore
        from riva.client.proto.riva_audio_pb2 import AudioEncoding  # type: ignore
    except Exception:
        raise SttUnavailable("nvidia riva client not installed") from None
    auth = riva.client.Auth(uri="grpc.nvcf.nvidia.com:443", use_ssl=True,
                            metadata_args=[["function-id", function_id], ["authorization", f"Bearer {key}"]],
                            options=[("grpc.max_receive_message_length", 32 * 1024 * 1024)])
    return riva.client.SpeechSynthesisService(auth), AudioEncoding


def _chatterbox_grpc_chunk(text: str, profile: dict, key: str, function_id: str, timeout_s: float, service=None, audio_encoding=None) -> bytes:
    """One Chatterbox chunk over the hosted Riva gRPC API; the response is raw PCM, so wrap it as WAV."""
    if service is None or audio_encoding is None:
        service, audio_encoding = _riva_service(key, function_id)
    rate = int(profile.get("rate") or 22050)
    try:
        future = service.synthesize(text=text, voice_name=profile.get("voice"), language_code=profile["language"],
                                    encoding=audio_encoding.LINEAR_PCM, sample_rate_hz=rate, future=True)
        resp = future.result(timeout=max(5.0, float(timeout_s)))
    except Exception as e:
        raise SttUnavailable(f"nvidia chatterbox grpc {type(e).__name__}") from None
    pcm = getattr(resp, "audio", b"")
    if not pcm:
        raise SttUnavailable("nvidia chatterbox empty")
    return _pcm16_wav(pcm, rate)


def nvidia_tts(text: str, lang: str, s: Settings) -> bytes:
    """NVIDIA hosted TTS as one WAV. ru uses Chatterbox gRPC; hi/vi use Magpie Multilingual HTTP."""
    profile = NVIDIA_TTS_PROFILES.get(lang)
    key = _nvidia_key(s)
    if not profile or not key or not getattr(s, "nvidia_tts_enabled", True):
        raise SttUnavailable(f"no nvidia tts for {lang}")
    prepared = ru_tts.prepare(text) if lang == "ru" else text
    chunks = tts_chunks(prepared, limit=450)  # Chatterbox hosted limit is about 500 chars; Magpie allows more
    if not chunks:
        raise SttUnavailable("nothing to speak")
    fid = (getattr(s, "nvidia_tts_chatterbox_function_id", "") if profile["provider"] == "chatterbox"
           else getattr(s, "nvidia_tts_magpie_function_id", ""))
    if not fid:
        raise SttUnavailable(f"no nvidia {profile['provider']} endpoint")
    timeout_s = getattr(s, "nvidia_tts_timeout_s", 60.0)
    if profile["provider"] == "chatterbox":
        service, audio_encoding = _riva_service(key, fid)  # one channel for all chunks of this answer
        parts = [_chatterbox_grpc_chunk(c, profile, key, fid, timeout_s, service, audio_encoding) for c in chunks]
        return _wav_join(parts) if len(parts) > 1 else parts[0]
    url = _nvidia_tts_url(fid)
    parts = []
    for chunk in chunks:
        fields = {"text": chunk, "language": profile["language"], "encoding": "LINEAR_PCM",
                  "sample_rate_hz": str(int(profile.get("rate") or 44100))}
        if profile.get("voice"):
            fields["voice"] = profile["voice"]
        try:
            r = httpx.post(url, headers={"Authorization": f"Bearer {key}"},
                           files={k: (None, v) for k, v in fields.items()}, timeout=timeout_s)
            r = _nvcf_wait(r, key, timeout_s)
        except httpx.HTTPError as e:
            raise SttUnavailable(f"nvidia tts network: {type(e).__name__}") from None
        if r.status_code != 200:
            raise SttUnavailable(f"nvidia tts {r.status_code}")
        if not r.content.startswith(b"RIFF"):
            raise SttUnavailable("nvidia tts returned non-wav")
        parts.append(r.content)
    return _wav_join(parts) if len(parts) > 1 else parts[0]


# Silero v5_cis_base_nostress (MIT) local/server voices. Speaker prefixes are the ones published by Silero;
# Tajik is intentionally local-only because SaluteSpeech does not cover it.
SILERO_TTS_VOICES = {"ru": "ru_zinaida", "kk": "kaz_zhadyra", "uz": "uzb_saida", "ky": "kir_nurgul", "tg": "tgk_onaoy"}


def server_tts_langs(s: Settings) -> list[str]:
    out = set()
    if getattr(s, "cloudru_api_key", "") and getattr(s, "cloudru_tts", True):
        out.update(cloudru_tts_langs(s))
    if _nvidia_key(s) and getattr(s, "nvidia_tts_enabled", True):
        out.update(NVIDIA_TTS_PROFILES)
    if getattr(s, "speech_url", "") or getattr(s, "silero_enabled", False):
        out.update(SILERO_TTS_VOICES)
    # hi/vi stay implemented in the provider layer for a possible future re-enable, but are not product languages now.
    return sorted(out & set(PRODUCT_LANGS))


def tts_configured(s: Settings) -> bool:
    return bool(server_tts_langs(s))


def tts_synthesize(text: str, lang: str, s: Settings) -> bytes:
    """Server voice for the answer language: Cloud.ru for Russian, NVIDIA where supported, Silero/browser as fallback."""
    lang = (lang or "ru").lower().split("-")[0]
    errors = []
    if lang in cloudru_tts_langs(s) and getattr(s, "cloudru_api_key", "") and getattr(s, "cloudru_tts", True):
        try:
            return cloudru_tts(text, lang, s)
        except SttUnavailable as e:
            errors.append(str(e))
    if lang in NVIDIA_TTS_PROFILES and _nvidia_key(s) and getattr(s, "nvidia_tts_enabled", True):
        try:
            return nvidia_tts(text, lang, s)
        except SttUnavailable as e:
            errors.append(str(e))
    if lang in SILERO_TTS_VOICES:
        try:
            return silero_tts(text, s, lang)
        except SttUnavailable as e:
            errors.append(str(e))
    raise SttUnavailable("; ".join(errors) or f"no server tts for {lang}")


def silero_tts(text: str, s: Settings, lang: str = "ru") -> bytes:
    """Local Silero CIS TTS (speech service or in-process CPU), sentence by sentence.
    Russian gets digit/unit spelling and stress; the other CIS languages are sent in their own script."""
    global _silero
    chunks = tts_chunks(text)
    if not chunks:
        raise SttUnavailable("nothing to speak")
    lang = (lang or "ru").lower().split("-")[0]
    speaker = SILERO_TTS_VOICES.get(lang)
    if not speaker:
        raise SttUnavailable(f"no silero voice for {lang}")
    if s.speech_url:
        parts = []
        try:
            for c in chunks:
                r = httpx.post(f"{s.speech_url.rstrip('/')}/tts", json={"text": c, "speaker": speaker}, timeout=60)
                if r.status_code == 200:
                    parts.append(r.content)
        except httpx.HTTPError:
            pass
        if parts:
            return _wav_join(parts)
    if not s.silero_enabled:
        raise SttUnavailable("silero disabled")
    import torch  # type: ignore

    if _silero is None:
        _silero, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker=ru_tts.TTS_MODEL, trust_repo=True)  # MIT
    import wave

    audio = []
    for c in chunks:
        try:
            audio.append(_silero.apply_tts(text=ru_tts.prepare(c) if lang == "ru" else c, speaker=speaker, sample_rate=24000))
        except Exception:
            continue
    if not audio:
        raise SttUnavailable("silero could not speak the text")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"".join((a.clamp(-1, 1) * 32767).short().numpy().tobytes() for a in audio))
    return buf.getvalue()
