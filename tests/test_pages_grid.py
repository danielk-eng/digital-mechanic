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


# ЗМЗ-5234 РК p. 151/153, ЗМЗ-409 РК p. 124: tolerances set one over the other, signs as Symbol glyphs (U+F02D «−», U+F02B «+»)
_STACK_151 = """<flow><block xMin="208.58" yMin="198.99" xMax="252.92" yMax="219.44">
<line xMin="243.428646" yMin="201.931933" xMax="252.929180" yMax="210.147703"><word xMin="243.428646" yMin="201.931933" xMax="252.929180" yMax="210.147703">03</word></line>
<line xMin="208.580000" yMin="198.992500" xMax="252.713170" yMax="219.443453">
<word xMin="208.580000" yMin="204.060680" xMax="229.625960" yMax="216.696680">100</word>
<word xMin="231.481211" yMin="198.992500" xMax="236.433469" yMax="219.434311">\uf02d\uf02d</word>
<word xMin="236.375301" yMin="201.931926" xMax="240.885544" yMax="210.147695">0</word>
<word xMin="236.999086" yMin="211.227676" xMax="241.509329" yMax="219.443446">0</word>
<word xMin="240.981361" yMin="201.931931" xMax="243.236483" yMax="210.147700">,</word>
<word xMin="241.605435" yMin="211.227681" xMax="243.860556" yMax="219.443451">,</word>
<word xMin="243.212635" yMin="211.227683" xMax="252.713170" yMax="219.443453">10</word>
</line></block></flow>"""
_STACK_153 = """<flow><block xMin="209.66" yMin="252.04" xMax="252.83" yMax="272.49">
<line xMin="209.660000" yMin="252.042501" xMax="252.694554" yMax="272.493449">
<word xMin="209.660000" yMin="257.100680" xMax="223.760000" yMax="269.736680">40</word>
<word xMin="225.631290" yMin="261.338250" xMax="230.583710" yMax="272.484311">\uf02b</word>
<word xMin="226.375097" yMin="252.042501" xMax="231.327517" yMax="263.188562">\uf02b</word>
<word xMin="231.245355" yMin="254.981925" xMax="235.755755" yMax="263.197695">0</word>
<word xMin="231.389283" yMin="264.277675" xMax="235.899684" yMax="272.493445">0</word>
<word xMin="235.803858" yMin="254.981929" xMax="238.059058" yMax="263.197699">,</word>
<word xMin="235.947883" yMin="264.277679" xMax="238.203083" yMax="272.493449">,</word>
<word xMin="238.251228" yMin="254.981931" xMax="252.694554" yMax="263.197701">027</word>
</line>
<line xMin="238.394293" yMin="264.277681" xMax="252.837619" yMax="272.493451"><word xMin="238.394293" yMin="264.277681" xMax="252.837619" yMax="272.493451">009</word></line>
</block></flow>"""
_ONE_TOL = """<flow><block xMin="1" yMin="1" xMax="99" yMax="20"><line xMin="279.29" yMin="186.87" xMax="327.57" yMax="203.03">
<word xMin="279.290000" yMin="186.870680" xMax="293.390000" yMax="199.506680">60</word>
<word xMin="295.432768" yMin="188.800417" xMax="301.676495" yMax="203.026779">\uf02b</word>
<word xMin="301.950907" yMin="192.552178" xMax="307.637362" yMax="203.038440">0</word>
<word xMin="307.596869" yMin="192.552187" xMax="310.440096" yMax="203.038448">,</word>
<word xMin="310.596174" yMin="192.552191" xMax="327.573678" yMax="203.038453">019</word>
</line></block></flow>"""


def test_stacked_tolerances_keep_signs_and_rows():
    from digital_mechanic.ingest.pages import _xml_lines, _tol_order
    assert [l[4] for l in _xml_lines(_STACK_151)] == ["100 −0,03 −0,10"]  # was «03 100 0 0 , , 10»
    assert [l[4] for l in _xml_lines(_STACK_153)] == ["40 +0,027 +0,009"]  # two glyphs: the lower one comes first in the file
    assert [l[4] for l in _xml_lines(_ONE_TOL)] == ["60 +0,019"]  # was «60  0 , 019»
    assert _tol_order("передней — +0,050 49 +0,025 | 49,1") == "передней — 49 +0,050 +0,025 | 49,1"
    assert _tol_order("62 −0,035 −0,054 | 61,92") == "62 −0,035 −0,054 | 61,92"  # already in order


def test_plain_lines_untouched():
    from digital_mechanic.ingest.pages import _xml_lines
    x = """<flow><block xMin="1" yMin="1" xMax="99" yMax="20"><line xMin="10" yMin="10" xMax="90" yMax="20">
<word xMin="10" yMin="10" xMax="40" yMax="20">Гильза</word><word xMin="42" yMin="10" xMax="44" yMax="20">-</word><word xMin="46" yMin="10" xMax="90" yMax="20">диаметр</word></line></block></flow>"""
    assert [l[4] for l in _xml_lines(x)] == ["Гильза - диаметр"]
