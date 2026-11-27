"""Silero v5_cis_base_nostress (MIT) front-end: digits and units in words, the same file in both speech services."""
from pathlib import Path

from digital_mechanic import ru_tts

ROOT = Path(__file__).resolve().parents[1]


def test_numbers_units_in_words():
    t = ru_tts.normalize("Давление 400-700 кПа при 2100 об/мин [S1]. Момент 43–46 кгс·м, зазор 0,25 мм.")
    assert not any(ch.isdigit() for ch in t) and "[S1]" not in t
    assert "от четыреста до семьсот килопаскаль" in t and "две тысячи сто оборотов в минуту" in t
    assert "от сорок три до сорок шесть килограмм-сила-метр" in t and "ноль и двадцать пять сотых миллиметра" in t
    assert ru_tts.int_words(1001) == "одна тысяча один" and ru_tts.int_words(22) == "двадцать два"


def test_licence_and_copies():
    assert ru_tts.TTS_MODEL == "v5_cis_base_nostress" and ru_tts.DEFAULT_SPEAKER.startswith("ru_")
    src = (ROOT / "src/digital_mechanic/ru_tts.py").read_text(encoding="utf-8")
    for p in ("services/speech/ru_tts.py", "services/speech_gpu/ru_tts.py"):
        assert (ROOT / p).read_text(encoding="utf-8") == src, p
    for p in ROOT.joinpath("services").rglob("*.py"):
        assert '"v4_ru"' not in p.read_text(encoding="utf-8"), p
    assert '"v4_ru"' not in (ROOT / "src/digital_mechanic/voice.py").read_text(encoding="utf-8")
