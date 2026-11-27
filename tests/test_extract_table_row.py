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


def test_llm_extract_survives_restart(make_agent, index):
    """The LLM-written extract is kept in SQLite: after a restart (deb) the same citation opens at once, no LLM call."""
    from digital_mechanic.agent.core import _TABLE_HEAD_RE
    a = make_agent([msg_("Проверьте тепловые зазоры клапанов на холодном двигателе.")])
    pg = next(p for p in index.pages if len(p.text) > 800 and not _TABLE_HEAD_RE.search(p.text) and a.sections.find(p.doc_id, p.page, p.text[200:260]))
    hint = pg.text[200:260]
    x = a.section_extract(pg.doc_id, pg.page, hint, "как проверить зазоры")
    assert x["ok"] and len(a.llm.calls) == 1
    b = make_agent([])
    b.store = a.store
    y = b.section_extract(pg.doc_id, pg.page, hint, "как проверить зазоры")
    assert y == x and b.llm.calls == []


def msg_(content):
    from types import SimpleNamespace
    return SimpleNamespace(content=content, tool_calls=None)


def test_extract_leadin_dropped():
    from digital_mechanic.agent.core import _drop_leadin
    assert _drop_leadin("На стр. 268 указано, что не являются браковочными признаками потение в соединениях.") == "Не являются браковочными признаками потение в соединениях."
    assert _drop_leadin("Согласно таблице, зазор регулируют на холодном двигателе.") == "Зазор регулируют на холодном двигателе."
    assert _drop_leadin("На странице: зазор 0,25–0,30 мм на холодном двигателе.") == "Зазор 0,25–0,30 мм на холодном двигателе."
    t = "Натяжение ремней контролировать регулярно."
    assert _drop_leadin(t) == t
