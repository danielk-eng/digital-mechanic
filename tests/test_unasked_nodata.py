"""Vercel audit 23:17 (C1 explanation, ЯМЗ-236): «Числовых допусков по углу и зазорам в найденных фрагментах нет — в руководстве
для этого двигателя данных нет» while РЭ p. 31 gives the valve clearances. A cited answer drops such a sentence; a refusal stays."""
from digital_mechanic.agent.core import drop_unasked_nodata


def test_cited_answer_drops_fragment_nodata_sentence():
    a = ("**Проверить:** угол опережения [S1]; тепловые зазоры клапанов [S2].\n"
         "Числовых допусков по углу и зазорам в найденных фрагментах нет — в руководстве для этого двигателя данных нет, нужна дефектовка.\n"
         "Заявку на дефектовку подготовлю — подтверди кнопкой.")
    out = drop_unasked_nodata(a)
    assert "фрагмент" not in out and "данных нет" not in out
    assert out.startswith("**Проверить:**") and out.endswith("подтверди кнопкой.")


def test_same_line_keeps_other_sentences():
    a = "Стук — ранний впрыск [S1]. Числовых допусков во фрагменте нет — по [S1] не приводятся. Отрегулируй угол [S2]."
    assert drop_unasked_nodata(a) == "Стук — ранний впрыск [S1]. Отрегулируй угол [S2]."


def test_refusal_and_plain_answers_untouched():
    r = "В руководстве для этого двигателя данных нет. Обратитесь к мастеру."
    assert drop_unasked_nodata(r) == r
    a = "Момент затяжки 120 Н·м [S1]. Порядок — от центра [S2]."
    assert drop_unasked_nodata(a) == a
