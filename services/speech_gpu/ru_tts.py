"""Russian TTS text front-end for Silero v5_cis_base_nostress (MIT licence; the earlier v4_ru is CC BY-NC-SA and must
not be used commercially). That model reads neither digits nor stress marks by itself, so:
1. numbers, ranges and units are spelled out in words (it silently drops digits: «400-700 кПа» was read as «кПа»);
2. stress is placed by silero-stress (MIT, `pip install silero-stress`) as «давл+ение»; without it the text is read as is.
The same file is copied into services/speech and services/speech_gpu (tests/test_ru_tts.py keeps the copies identical).
"""
from __future__ import annotations

import re

TTS_MODEL = "v5_cis_base_nostress"
DEFAULT_SPEAKER = "ru_zinaida"  # whisper round-trip 27.11: every word and number recognised (ru_dmitriy: «балты», ru_ekaterina: «кПП»)

_UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UNITS_F = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]


def _triad(n: int, fem: bool = False) -> list[str]:
    out = [_HUNDREDS[n // 100]]
    t = n % 100
    if 10 <= t < 20:
        out.append(_TEENS[t - 10])
    else:
        out += [_TENS[t // 10], (_UNITS_F if fem else _UNITS)[t % 10]]
    return [w for w in out if w]


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return one if n % 10 == 1 else few if n % 10 in (2, 3, 4) else many


def int_words(n: int) -> str:
    if n == 0:
        return "ноль"
    if n < 0:
        return "минус " + int_words(-n)
    words = []
    for div, forms, fem in ((10 ** 9, ("миллиард", "миллиарда", "миллиардов"), False), (10 ** 6, ("миллион", "миллиона", "миллионов"), False),
                            (1000, ("тысяча", "тысячи", "тысяч"), True)):
        if n >= div:
            k = n // div
            words += _triad(k, fem) + [_plural(k, *forms)]
            n %= div
    words += _triad(n)
    return " ".join(words)


def num_words(s: str) -> str:
    s = s.replace(" ", "")
    if re.fullmatch(r"\d+[.,]\d+", s):
        a, b = re.split(r"[.,]", s)
        return f"{int_words(int(a))} и {int_words(int(b))} " + {1: "десятых", 2: "сотых", 3: "тысячных"}.get(len(b), "")
    return int_words(int(s))


_UNIT_WORDS = [
    (r"кгс\s*[·*.⋅]?\s*м", "килограмм-сила-метр"), (r"кгс\s*/\s*см\s*(?:2|²)", "килограмм-сила на квадратный сантиметр"),
    (r"Н\s*[·*.⋅]?\s*м|Нм", "ньютон-метр"), (r"кПа", "килопаскаль"), (r"МПа", "мегапаскаль"), (r"мм", "миллиметра"),
    (r"об\s*/\s*мин|мин\s*[-−]1", "оборотов в минуту"), (r"°\s*C|°\s*С|ºС|ºC", "градусов"), (r"чел\.?\s*-?\s*ч", "человеко-часа"),
    (r"%", "процентов"), (r"стр\.", "страница"), (r"с\.(?=\s*\d)", "страница"),
]


def normalize(text: str) -> str:
    t = re.sub(r"\[(?:S|X)\d+\]", "", text or "")
    t = re.sub(r"[*_#>`]", "", t)
    for rx, w in _UNIT_WORDS:
        t = re.sub(rf"(?<![А-Яа-яЁёA-Za-z]){rx}(?![А-Яа-яЁёA-Za-z])", " " + w + " ", t)
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*[-–—…]\s*(\d)", r"от \1 до \2", t)  # 400–700 -> «от четыреста до семьсот»
    t = re.sub(r"\d+(?:[.,]\d+)?", lambda m: " " + num_words(m.group(0)) + " ", t)
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"\(\s+", "(", re.sub(r"\s+([.,;:!?)])", r"\1", t)).strip()


_acc = None


def stress(text: str) -> str:
    global _acc
    if _acc is None:
        try:
            from silero_stress import load_accentor  # type: ignore

            _acc = load_accentor()
        except Exception:
            _acc = False
    return _acc(text) if _acc else text


def prepare(text: str) -> str:
    return stress(normalize(text))
