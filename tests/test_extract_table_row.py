"""Citation extract on a fault table: only the cited row's symptom group (p. 53 used to retell «пониженное давление масла» for the valve row)."""
from digital_mechanic.agent.core import table_row_group

PAGE = """Причина — Способ устранения
Износ поршневых колец — Заменить поршневые кольца
Двигатель стучит
Ранний впрыск топлива в цилиндры — Отрегулировать угол опережения впрыскивания топлива
Нарушена регулировка клапанного механизма — Отрегулировать тепловые зазоры в клапанном механизме
Пониженное давление масла в системе смазки*
Неисправен манометр — Заменить исправным
Засорение заборника масляного насоса — Промыть заборчик масляного насоса
53"""


def test_valve_row_stays_in_its_group():
    head, rows, i = table_row_group(PAGE, "Нарушена регулировка клапанного механизма — Отрегулировать тепловые зазоры")
    assert head == "Двигатель стучит" and "клапанного" in rows[i]
    assert not any("манометр" in r for r in rows)


def test_heading_hint_and_non_table():
    head, rows, i = table_row_group(PAGE, "пониженное давление масла в системе смазки")
    assert head == "Пониженное давление масла в системе смазки" and i == -1 and len(rows) == 2
    assert table_row_group("Обычный текст страницы про зазоры клапанов", "зазоры клапанов") is None


def test_long_answer_hint_uses_the_clause_before_the_chip():
    hint = "Работы: прокачать топливную и убрать подсос (с. 52); промыть форсунки (с. 52); отрегулировать тепловые зазоры клапанов"
    head, rows, i = table_row_group(PAGE, hint)
    assert head == "Двигатель стучит" and "клапанного" in rows[i]


def test_table_headings_when_no_row_matches():
    from digital_mechanic.agent.core import table_headings
    assert table_headings(PAGE) == [("Продолжение с предыдущей страницы", 1), ("Двигатель стучит", 2), ("Пониженное давление масла в системе смазки", 2)]
