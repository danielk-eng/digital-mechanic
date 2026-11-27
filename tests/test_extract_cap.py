from digital_mechanic.agent.core import _cap_extract


def test_long_prose_is_cut_to_four_sentences():
    t = "Первое. Второе 421 Н·м. Третье! Четвёртое? Пятое. Шестое."
    assert _cap_extract(t) == "Первое. Второе 421 Н·м. Третье! Четвёртое?"


def test_short_text_and_decimals_untouched():
    t = "Давление 4.5 кгс/см2 при 2100 об/мин. Проверить манометром."
    assert _cap_extract(t) == t


def test_list_keeps_lead_and_five_points():
    t = "Причины:\n" + "\n".join(f"- пункт {i}" for i in range(1, 9)) + "\nЕщё абзац."
    out = _cap_extract(t)
    assert out.startswith("Причины:") and "пункт 5" in out and "пункт 6" not in out and "абзац" not in out


def test_long_sentences_capped_by_length():
    a = "Стук грозит переборкой. "; b = "Потение " + "слово " * 120 + "конец. "; c = "Не бракуются выделения."
    out = _cap_extract(a + b + c)
    assert out == a.strip()
    assert _cap_extract(b.strip()) == b.strip()  # a single long sentence is kept whole
