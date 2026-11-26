import pytest


def test_all_six_manuals_indexed(pages):
    counts = {}
    for p in pages:
        counts[p.doc_id] = counts.get(p.doc_id, 0) + 1
    assert counts == {"YAMZ236-RE": 172, "YAMZ236-RK": 287, "YAMZ536-RE": 285, "YAMZ536-RK": 603, "ZMZ5234-RK": 158, "ZMZ409-RK": 238}


@pytest.mark.parametrize("engine", ["ЯМЗ-236М2", "ЯМЗ-536", "ЗМЗ-5234.10", "ЗМЗ-40904.10", "ЗМЗ-40905.10"])
def test_engine_filter_never_leaks(index, engine):
    for q in ("момент затяжки крышек коренных подшипников", "давление масла", "тепловой зазор клапанов"):
        for h in index.search(engine, q, k=10):
            assert engine in h.page.engine_types


@pytest.mark.parametrize("engine,doc,page,needle", [
    ("ЯМЗ-236М2", "YAMZ236-RK", 41, "421-461"),
    ("ЗМЗ-5234.10", "ZMZ5234-RK", 137, "98…107,8"),
    ("ЗМЗ-40905.10", "ZMZ409-RK", 230, "98…107,9"),
])
def test_main_bearing_torque_found(index, engine, doc, page, needle):
    hits = index.search(engine, "момент затяжки болтов крышек коренных подшипников", k=5)
    assert any(h.page.doc_id == doc and h.page.page == page for h in hits)
    assert needle in index.get_page(doc, page).text


def test_references_use_official_designations(pages):
    titles = {p.document for p in pages}
    assert "236.01.01 РК" in titles and "536.3902150 РЭ" in titles


def test_kamaz_mirror_excluded():
    import gzip, json
    from digital_mechanic.retrieval.bm25 import load_extra
    pages = load_extra("data/index/extra_pages.jsonl.gz")
    assert pages and all(p.make != "КАМАЗ" and p.source != "avtoalfa.com" for p in pages)
    raw = [json.loads(l) for l in gzip.open("data/index/extra_pages.jsonl.gz", "rt", encoding="utf-8")]
    assert not any("KAMAZ" in r["doc_id"] for r in raw)
