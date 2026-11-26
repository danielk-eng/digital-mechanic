from digital_mechanic.agent.grounding import cited_labels, quote_in_text, unverified_numbers
from digital_mechanic import pii

PAGE = "затяжку болтов крепления крышек производить с моментом\n421-461 Н*м (43-47 кгс*м) последовательно в два приема"


def test_quote_tolerates_linebreaks_and_symbols():
    assert quote_in_text("с моментом 421-461 Н*м (43-47 кгс*м)", PAGE)
    assert not quote_in_text("с моментом 500-600 Н*м", PAGE)


def test_number_guard():
    assert unverified_numbers("Момент 421-461 Н·м [S1]", [PAGE]) == []
    assert unverified_numbers("Момент 480 Н·м [S1]", [PAGE]) == ["480"]
    assert unverified_numbers("Шаг 3 из 10, стр. 241 [S2]", [PAGE]) == []


def test_cited_labels_loose():
    assert cited_labels("a [S1] b [S2, стр. 41] c [S3; X1]") == ["S1", "S2", "S3", "X1"]


def test_pii_mask():
    t = pii.mask("Иванов И.И., тел. +7 912 345-67-89, mail a.b@c.ru, паспорт 4510 123456")
    assert "912" not in t and "@" not in t and "123456" not in t and "Иванов" not in t


def test_model_designations_are_not_norms():
    assert unverified_numbers("В базе только ЯМЗ-236М2/238М2 и ЗМЗ-40904.10", []) == []
    assert unverified_numbers("Момент 480 Н·м для ЯМЗ-536", []) == ["480"]


def test_repeat_detector():
    from digital_mechanic.agent.core import _same
    a = "**Не менее 100 кПа** на холостом ходу [S1]. После длительной эксплуатации 50 кПа [S1]."
    assert _same(a, a + " ")
    assert not _same(a, "Данных для ЗМЗ-409 нет: это другой двигатель.")
