from digital_mechanic.retrieval.bm25 import ExtraIndex, load_extra


def test_new_official_chassis_manuals_indexed_and_model_first():
    ex = ExtraIndex(load_extra("data/index/extra_pages.jsonl.gz"))
    docs = {p.doc_id for p in ex.pages}
    assert {"EXTRA-URAL4320-RE", "EXTRA-URAL43206-RE", "EXTRA-UAZPATRIOT-RE"} <= docs and "EXTRA-KAMAZ4350-RE" not in docs
    assert "УАЗ" in ex.makes() and "УРАЛ" in ex.makes()
    p = next(p for p in ex.pages if p.doc_id == "EXTRA-URAL4320-RE" and p.page == 41)
    assert sum("а" <= ch <= "я" for ch in p.text.lower()) > 100  # cp1251 text layer decoded
    hits = ex.search("УАЗ", "давление в шинах", k=3)
    assert hits and hits[0][0].doc_id == "EXTRA-UAZPATRIOT-RE"
