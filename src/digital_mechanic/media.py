"""User media from the phone: photos, short videos (keyframes + audio track) and engine sound recordings.

The acoustic ML model (NeuroDVS, ТЗ §5) takes only sessions of the stand sensors (vehicle_id + session_id),
not a raw phone recording. So a phone recording is NOT a diagnosis: we keep it with the vehicle history,
compute honest signal features (length, level, clipping, main tone, unevenness) and let the agent
answer by the manual. No numpy: 16 kHz mono WAV, at most 30 s, pure Python is fast enough.
"""

from __future__ import annotations

import array
import io
import math
import re
import wave

MAX_SECONDS = 30
CYLINDERS = {"ЯМЗ-236": 6, "ЯМЗ-238": 8, "ЯМЗ-240": 12, "ЯМЗ-7511": 8, "ЯМЗ-536": 6, "КАМАЗ-740": 8, "Д-245": 4, "ММЗ": 4}


def cylinders(engine_model: str | None) -> int | None:
    m = (engine_model or "").upper().replace(" ", "")
    for k, n in CYLINDERS.items():
        if k.upper().replace(" ", "") in m:
            return n
    return None


def read_wav(data: bytes) -> tuple[list[float], int]:
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(min(n, sr * MAX_SECONDS))
    if sw != 2:
        raise ValueError("only 16-bit PCM WAV")
    a = array.array("h", raw)
    if ch > 1:
        a = array.array("h", (sum(a[i:i + ch]) // ch for i in range(0, len(a) - ch + 1, ch)))
    return [x / 32768.0 for x in a], sr


def _goertzel_power(x: list[float], sr: int, f: float) -> float:
    k = 2 * math.cos(2 * math.pi * f / sr)
    s1 = s2 = 0.0
    for v in x:
        s1, s2 = v + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


def features(data: bytes, engine_model: str | None = None) -> dict:
    """Signal features of an engine recording. Raises ValueError on a non-WAV payload."""
    x, sr = read_wav(data)
    dur = len(x) / sr if sr else 0.0
    if not x:
        return {"duration_s": 0, "quality": "bad", "quality_ru": "пустая запись", "notes": ["Запись пустая."]}
    rms = math.sqrt(sum(v * v for v in x) / len(x))
    rms_db = 20 * math.log10(rms) if rms > 0 else -120.0
    clip = sum(1 for v in x if abs(v) > 0.98) / len(x)
    # envelope over 50 ms windows: unevenness = coefficient of variation (misfire / knock make it jump)
    win = max(1, int(sr * 0.05))
    env = [math.sqrt(sum(v * v for v in x[i:i + win]) / win) for i in range(0, len(x) - win + 1, win)]
    mean = sum(env) / len(env) if env else 0
    cv = (math.sqrt(sum((e - mean) ** 2 for e in env) / len(env)) / mean) if mean > 0 else 0
    # main low tone 15..250 Hz on a 2 s slice decimated to 1 kHz (firing frequency of a diesel at idle..rated)
    step = max(1, sr // 1000)
    mid = len(x) // 2
    seg = x[max(0, mid - sr):mid + sr:step]
    srd = sr / step
    best_f, best_p, tot = 0.0, 0.0, 0.0
    for f in [15 + 0.5 * i for i in range(471)]:
        p = _goertzel_power(seg, srd, f)
        tot += p
        if p > best_p:
            best_f, best_p = f, p
    tonal = best_p / (tot / 471) if tot else 0
    out = {"duration_s": round(dur, 1), "rms_db": round(rms_db, 1), "clipping_pct": round(clip * 100, 2),
           "unevenness": round(cv, 2), "main_tone_hz": round(best_f, 1) if tonal > 4 else None}
    cyl = cylinders(engine_model)
    if out["main_tone_hz"] and cyl:
        rpm = out["main_tone_hz"] * 120 / cyl  # 4-stroke: firing frequency = rpm/60 * cyl/2
        if 400 <= rpm <= 3000:
            out["rpm_estimate"] = int(round(rpm, -1))
    notes = []
    if dur < 3:
        notes.append("Запись короче 3 с: для сравнения нужны 10–20 с.")
    if rms_db < -45:
        notes.append("Очень тихо: поднесите телефон ближе к двигателю.")
    if clip > 0.01:
        notes.append("Перегруз микрофона: отодвиньте телефон или закройте от ветра.")
    if cv > 0.3:
        notes.append("Громкость заметно скачет: похоже на неравномерную работу или стук, либо на помехи.")
    q = "bad" if (dur < 2 or rms_db < -55) else "warning" if notes else "good"
    out.update(quality=q, quality_ru={"good": "хорошая", "warning": "есть замечания", "bad": "плохая"}[q], notes=notes)
    return out


def describe_sound(f: dict) -> str:
    """Russian text for the agent: what the recording has and what it is NOT."""
    s = (f"Запись звука двигателя с телефона: {f.get('duration_s', 0)} с, уровень {f.get('rms_db', '?')} дБ, "
         f"качество записи: {f.get('quality_ru', '?')}")
    if f.get("main_tone_hz"):
        s += f", основной тон {f['main_tone_hz']} Гц"
    if f.get("rpm_estimate"):
        s += f" (по нему обороты примерно {f['rpm_estimate']} об/мин)"
    s += f", неравномерность громкости {f.get('unevenness', 0)}."
    if f.get("notes"):
        s += " " + " ".join(f["notes"])
    return s + (" Это не результат ML-диагностики: модель НейроДВС принимает только сессии стендовых датчиков. "
                "Запись приложена к истории машины.")


_JUNK = re.compile(r"(?i)^(продолжение следует|субтитры|спасибо за просмотр|редактор субтитров|\W*)\W*$")


def meaningful_speech(text: str) -> str:
    """Whisper invents phrases on pure engine noise; keep only real speech."""
    t = (text or "").strip()
    if len(t.split()) < 3 or _JUNK.match(t) or "субтитр" in t.lower():
        return ""
    return t
