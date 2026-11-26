from datetime import date
from pathlib import Path

from digital_mechanic.history import load_demo, mask_name, summary_for_agent, timeline

ROOT = Path(__file__).resolve().parent.parent


def test_mask_name():
    assert mask_name("Сидоров П.") == "С***в П."
    assert mask_name("Иван") == "И***н" and mask_name("—") is None


def test_timeline_recurring_repair_recheck(make_agent):
    a = make_agent([])
    a.demo = load_demo(ROOT / "data/demo_history.json")
    tl = a.timeline("O710HH78")  # F04 twice, demo repair after S-0015, re-check S-0016 failed
    assert {r["anomaly_type"] for r in tl["recurring"]} == {"F04"}
    e = next(x for x in tl["entries"] if x["session_id"] == "S-0015")
    assert e["recurring"] and e["demo"] and e["ticket"]["ticket_id"] == "TKT-DEMO-102"
    assert e["repair"]["done"] == e["repair"]["total"] == 3 and e["parts"] == ["Ремень привода вентилятора"]
    assert e["recheck"]["session_id"] == "S-0016" and e["mechanic"] == "И***в А."
    assert tl["entries"][0]["date"] >= tl["entries"][-1]["date"]  # newest first
    assert all(x["mileage_km"] for x in tl["entries"])
    tl2 = timeline(a.fleet.get("O710HH78"), a.fleet.sessions("O710HH78"), a.store, a.demo, today=date(2026, 11, 26))
    assert tl2["days_since_last_check"] == 8
    s = summary_for_agent(tl)
    assert "ПОВТОРЯЮЩИЕСЯ" in s and "TKT-DEMO-102" in s


def test_history_in_context_and_chips(make_agent):
    a = make_agent([])
    a.demo = load_demo(ROOT / "data/demo_history.json")
    c = a.conv("u1", "B418HK98")
    a.diagnose(c, "S-0009")  # F01 again after a demo repair (TKT-DEMO-101)
    assert "TKT-DEMO-101" in a._context(c)
    chips = a.suggestions(c, {"lang": "ru", "experience": "experienced"})
    assert "TKT-DEMO-101" in chips[0]["text"]


def test_real_ticket_and_session_answer_in_timeline(make_agent):
    from conftest import msg, tool_call
    a = make_agent([msg(None, [tool_call("search_manual", {"query": "тепловой зазор клапанов"}, 1)]), msg("Зазор 0,25-0,30 мм [S1]")])
    c = a.conv("u1", "M333YK47")
    a.store.upsert_user("u1", name="Пётр Смирнов", role="mechanic")
    a.diagnose(c, "S-0005")
    turn = a.handle(c, {"lang": "ru"}, "зазоры?")
    a.store.add_message(c.cid, "u1", "M333YK47", "assistant", turn.text, {"sources": turn.sources})
    e = next(x for x in a.timeline("M333YK47")["entries"] if x["session_id"] == "S-0005")
    assert e["mechanic"] == "П***р С." and "0,25" in e["answer"] and e["sources"]
