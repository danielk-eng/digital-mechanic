"""Speech. STT order: local speech service (faster-whisper large-v3-turbo, audio stays on the stand) ->
Deepgram nova-3 (cloud, ru/hi/vi, mip_opt_out) -> in-process faster-whisper -> browser Web Speech (client side).
TTS: local speech service (Silero, ru) -> in-process Silero -> browser speech (client side).

Audio is not stored; only the masked transcript goes to the agent. Voice control in the UI also
works in the browser (Web Speech API) when the server has no STT configured.
"""

from __future__ import annotations

import io
import re
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
DEEPGRAM_LANGS = {"ru", "hi", "vi", "uz", "kk"}
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


def speech_stt(audio: bytes, mime: str, lang: str, s: Settings) -> str:
    if not s.speech_url:
        raise SttUnavailable("no speech service")
    r = httpx.post(f"{s.speech_url.rstrip('/')}/stt", files={"audio": ("a", audio, mime or "audio/webm")},
                   data={"lang": lang, "prompt": prompt_for(lang)}, timeout=s.speech_timeout_s)
    if r.status_code != 200:
        raise SttUnavailable(f"speech {r.status_code}")
    return postprocess(r.json()["text"].strip(), lang)


def transcribe(audio: bytes, mime: str, lang: str, s: Settings, local_first: bool = False) -> tuple[str, str]:
    """Return (text, engine): local whisper service -> Deepgram (ru/hi/vi) -> in-process whisper."""
    errors = []
    chain = [("whisper-local", lambda: speech_stt(audio, mime, lang, s))]
    if lang in DEEPGRAM_LANGS:
        dg = ("deepgram", lambda: deepgram_stt(audio, mime, lang, s))
        first = lang in DEEPGRAM_FIRST and s.stt_order == "cloud-first" and not local_first  # local-first (strict 152-FZ): audio stays on the stand
        chain.insert(0, dg) if first else chain.append(dg)
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
