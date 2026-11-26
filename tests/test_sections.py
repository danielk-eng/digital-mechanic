from types import SimpleNamespace

from digital_mechanic.retrieval.sections import SectionIndex, heading_of


def test_heading_rules():
    assert heading_of("9.3 Проверить зазор в стыке поршневых колец, зазор между кольцом и канавкой и установить кольца на поршень.")[1] == "9.3"
    assert heading_of("1. Заглушить двигатель.") is None          # numbered step, not a heading
    assert heading_of("5.11 Повышен уровень масла в") is None     # table row continued on the next line
    assert heading_of("1.2 Технические характеристики двигателей........") is None  # table of contents


def test_section_spans_pages(pages):
    s = SectionIndex(pages).find("ZMZ409-RK", 175, "Замерить тепловой зазор в стыке колец")
    assert s.num == "9.3" and s.start_page == 175 and s.end_page >= 176
    assert "0,20…0,40" in s.text


def test_yamz536_rk_modules(pages):
    s = SectionIndex(pages).find("YAMZ536-RK", 271, "")
    assert s.title.startswith("Установка насоса водяного") and s.start_page <= 271 <= s.end_page


def test_search_adds_section_and_next_page(make_agent):
    a = make_agent([])
    c = a.conv("u1", "B418HK98")
    v = c.vehicle
    out = a.tool_search_manual(c, "зазор в стыке поршневых колец", k=3)
    assert any(f.get("section") for f in out["fragments"])


def test_extract_drops_numbers_not_in_section(make_agent):
    a = make_agent([SimpleNamespace(content="Зазор в стыке колец 0,20…0,40 мм. Для работавшего кольца не более 7,7 мм.", tool_calls=None)])
    x = a.section_extract("ZMZ409-RK", 175, "Замерить тепловой зазор в стыке колец", "какой зазор колец")
    assert "0,20…0,40" in x["text"] and "7,7" not in x["text"] and x["dropped_numbers"]
