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


def test_every_fleet_model_has_its_vehicle_manual():
    """50/50: each model of the fleet gets pages of its own (or the labelled sibling) vehicle manual, not another model's."""
    import json
    from types import SimpleNamespace
    from digital_mechanic.agent.core import Agent
    ex = ExtraIndex(load_extra("data/index/extra_pages.jsonl.gz"))
    a = Agent.__new__(Agent); a.extra = ex
    want = {"МАЗ-5337": "EXTRA-MAZ555100-RE", "МАЗ-5551": "EXTRA-MAZ555100-RE", "МАЗ-54331": "EXTRA-MAZ555100-RE",
            "МАЗ-5340С2": "EXTRA-MAZ5440C9", "ПАЗ-3205": "EXTRA-PAZ32053-RE", "ПАЗ-3206": "EXTRA-PAZ32053-RE"}
    vs = json.load(open("data/official/vehicles.json"))["vehicles"]
    assert all(v["model"] for v in vs)
    for v in vs:
        hits = a.extra_hits(SimpleNamespace(make=v["make"], model=v["model"]), "давление воздуха в шинах")
        assert hits, v["vehicle_id"]
        if v["model"] in want:
            assert all(h[0].doc_id.startswith(want[v["model"]]) for h in hits), (v["model"], [h[0].doc_id for h in hits])


def test_chassis_question_prefetches_own_vehicle_manual(make_agent):
    from conftest import msg
    a = make_agent([msg("Свободный ход педали 29…35 мм [X1].")])
    c = a.conv("u1", "O819CY198")  # ПАЗ-3205: sibling ПАЗ-32053 manual
    turn = a.handle(c, {"lang": "ru"}, "Как отрегулировать свободный ход педали сцепления?")
    sent = a.llm.calls[-1]["messages"][-1]["content"]
    assert "ФРАГМЕНТЫ РУКОВОДСТВА ПО АВТОМОБИЛЮ" in sent and "[X1]" in sent
    assert any(t["tool"] == "search_extra_docs" and t.get("auto") for t in turn.tools)
    assert turn.sources and turn.sources[0]["doc_id"] == "EXTRA-PAZ32053-RE"
