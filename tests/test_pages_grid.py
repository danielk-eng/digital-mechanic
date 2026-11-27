"""Spec tables in 3+ columns (ЗМЗ-409 РК p. 124, ЗМЗ-5234 РК p. 151–156) were indexed column by column: all names,
then all sizes, so «Диаметр кривошипной головки шатуна» stood 30 lines away from «60,03». Rows are rebuilt."""
import yaml

from digital_mechanic.ingest.pages import reflow_grid


def _l(x0, y, text, x1=None):
    x1 = x1 or x0 + 6 * len(text)
    return (f'<line xMin="{x0}" yMin="{y}" xMax="{x1}" yMax="{y + 12}">'
            f'<word xMin="{x0}" yMin="{y}" xMax="{x1}" yMax="{y + 12}">{text}</word></line>')


PAGE = "".join([
    _l(110, 40, "Контролируемые параметры"), _l(300, 40, "Номинальный"), _l(300, 54, "размер, мм"), _l(420, 40, "Предельный"),
    _l(60, 80, "Диаметр шеек промежуточного вала:"),
    _l(60, 96, "передней"), _l(300, 96, "49 -0,041"), _l(420, 96, "48,95"),
    _l(60, 112, "задней"), _l(300, 112, "22 -0,013"), _l(420, 112, "21,95"),
    _l(60, 140, "Диаметр кривошипной головки"), _l(60, 154, "шатуна"), _l(300, 146, "60 +0,019"), _l(420, 146, "60,03"),
    _l(60, 180, "Отклонение от плоскостности"), _l(300, 180, "0,08"), _l(420, 180, "0,12"),
    _l(60, 210, "Ход поршня"), _l(300, 210, "86"), _l(420, 210, "–"),
    _l(60, 240, "Степень сжатия"), _l(300, 240, "9,5"), _l(420, 240, "–"),
    _l(290, 800, "124"),
])


def test_spec_table_rows_keep_name_and_sizes_together():
    t = reflow_grid(PAGE)
    assert t is not None
    lines = t.split("\n")
    assert lines[0] == "Контролируемые параметры | Номинальный размер, мм | Предельный"
    assert "передней — 49 -0,041 | 48,95" in lines
    assert "задней — 22 -0,013 | 21,95" in lines
    assert "Диаметр кривошипной головки шатуна — 60 +0,019 | 60,03" in lines  # wrapped name joined, sizes on its row
    assert "124" not in t  # page number dropped


def test_plain_text_page_is_not_a_grid():
    page = "".join(_l(60, 40 + 14 * i, f"Строка текста номер {i} без таблицы") for i in range(20))
    assert reflow_grid(page) is None


def test_grid_pages_are_listed_per_document():
    docs = yaml.safe_load(open("data/engine_docs.yaml"))["documents"]
    assert 124 in docs["ZMZ409-RK"]["grid_tables"] and 153 in docs["ZMZ5234-RK"]["grid_tables"]
