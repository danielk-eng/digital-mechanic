"""Vercel 27.11 12:30: «момент затяжки болтов головки блока ЗМЗ-409» on a ЯМЗ-536 truck was answered with ЯМЗ-536 numbers and no word
that they are for another engine. A fixed first line now says so; same-family names and answers that already say it stay as they are."""
from digital_mechanic.agent.answer_post import foreign_engine_note

ANS = "По книге болты головки тянут в три приёма [S4].\\n1 приём — 100±10 Н·м [S4]."


def test_foreign_engine_gets_a_note():
    out = foreign_engine_note("Какой момент затяжки болтов головки блока двигателя ЗМЗ-409?", "ЯМЗ-536", ANS)
    assert out.startswith("ЗМЗ-409 — не двигатель этой машины (у неё ЯМЗ-536)") and out.endswith(ANS)


def test_answer_that_already_says_it_is_untouched():
    a = "ЗМЗ-409 — не наш мотор, у этой машины ЯМЗ-536. " + ANS
    assert foreign_engine_note("момент затяжки ГБЦ ЗМЗ-409", "ЯМЗ-536", a) == a


def test_same_family_and_no_engine_untouched():
    assert foreign_engine_note("зазоры клапанов ЗМЗ-409", "ЗМЗ-40905.10", ANS) == ANS
    assert foreign_engine_note("давление масла ЯМЗ-236", "ЯМЗ-236М2", ANS) == ANS
    assert foreign_engine_note("Какой момент затяжки болтов головки?", "ЯМЗ-536", ANS) == ANS
    assert foreign_engine_note("ремонт по ЯМЗ 536", "ЯМЗ-536", ANS) == ANS


def test_latin_brand():
    assert foreign_engine_note("torque ZMZ-409 head", "ЯМЗ-536", ANS).startswith("ЗМЗ-409 — не двигатель")
