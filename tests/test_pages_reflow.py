"""Side figure captions are moved out of the text column (page viewer showed 'отверРис. 28')."""
from digital_mechanic.ingest.pages import _pick, reflow_side_captions


def _line(y, words):
    ws = "".join(f'<word xMin="{x0}" yMin="{y}" xMax="{x1}" yMax="{y + 8}">{w}</word>' for x0, x1, w in words)
    return f'<line xMin="0" yMin="{y}" xMax="0" yMax="{y + 8}">{ws}</line>'


PAGE = "".join([
    _line(100, [(68, 120, "Перед"), (124, 200, "установкой")]),
    _line(274, [(225, 300, "вставить"), (305, 352, "отвер-")]),
    _line(279, [(77, 95, "Рис."), (97, 110, "28."), (112, 158, "Крепление")]),
    _line(287, [(225, 244, "стие"), (249, 288, "передней")]),
    _line(290, [(114, 123, "на"), (126, 171, "двигателе:")]),
    _line(298, [(225, 244, "тора")]),
])


def test_side_caption_is_separated():
    t = reflow_side_captions(PAGE)
    assert t is not None
    body, cap = t.split("\n\n")
    assert body.split("\n") == ["Перед установкой", "вставить отвер-", "стие передней", "тора"]
    assert cap == "Рис. 28. Крепление\nна двигателе:"


def test_no_side_caption_keeps_pdftotext():
    page = _line(100, [(68, 120, "Рис."), (124, 140, "5.")]) + _line(120, [(68, 200, "текст")])
    assert reflow_side_captions(page) is None
    assert _pick("plain text", None) == "plain text"


def test_word_loss_falls_back_to_plain():
    assert _pick("a b c d e f g h i j", "a b") == "a b c d e f g h i j"


def test_glued_word_parts_are_joined():
    page = _line(10, [(68, 98, "выклю"), (98, 125, "чатель")]) + _line(10, [(225, 260, "текст")]) + _line(20, [(68, 90, "Рис."), (92, 100, "1.")])
    page += _line(20, [(225, 260, "дальше")])
    t = reflow_side_captions(page)
    assert t.startswith("выключатель текст")


def test_cause_remedy_table_is_rebuilt_row_by_row():
    from digital_mechanic.ingest.pages import reflow_table
    page = "".join([
        _line(59, [(116, 154, "Причина"), (236, 270, "Способ"), (272, 318, "устранения")]),
        _line(73, [(68, 110, "Наличие"), (113, 150, "воздуха"), (153, 197, "в"), (208, 260, "Прокачать"), (263, 352, "систему,")]),
        _line(84, [(68, 99, "системе"), (208, 252, "устранить")]),
        _line(94, [(68, 150, "Не"), (153, 197, "работает"), (208, 260, "Разобрать"), (263, 352, "насос")]),
        _line(104, [(68, 123, "насос")]),
        _line(179, [(116, 160, "Двигатель"), (163, 200, "не"), (203, 250, "развивает"), (253, 309, "мощности")]),
        _line(190, [(68, 197, "Загрязнение"), (208, 340, "Промыть")]),
        _line(528, [(68, 78, "52")]),
    ])
    text, g = reflow_table(page)
    assert 197 < g < 208
    assert text.split("\n") == ["Причина — Способ устранения", "Наличие воздуха в системе — Прокачать систему, устранить",
                                "Не работает насос — Разобрать насос", "Двигатель не развивает мощности", "Загрязнение — Промыть", "52"]


def test_not_a_table_page():
    from digital_mechanic.ingest.pages import reflow_table
    assert reflow_table(_line(10, [(68, 352, "Обычный"), (360, 400, "текст")]))[0] is None


def test_key_facts_take_one_table_row():
    from digital_mechanic.agent.facts import sentences
    t = "Причина — Способ устранения\nЗатрудненное перемещение рейки — Осторожно прогреть топливный насос\nЗагрязнение воздушного фильтра — Промыть фильтрующие элементы"
    assert sentences(t) == t.split("\n")[1:]
