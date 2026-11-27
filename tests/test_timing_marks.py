"""Organizers 27.11, C1 MAZ M333YK47 / S-0005 (F08, early injection): the timing-marks result of the inspection."""
import json

from conftest import msg, tool_call


def _ticket_from_5121(messages):
    for m in messages:
        if m.get("role") == "user" and "5.12.1" in str(m.get("content")):
            txt = m["content"]
            lab = txt.split("ФРАГМЕНТЫ РУКОВОДСТВА")[1].split("[", 1)[1].split("]", 1)[0]
            return msg(None, [tool_call("prepare_ticket", {"conclusion": "Ранний впрыск: метки угла опережения не совпадают", "operations": [
                {"source": lab, "quote": "регулировка угла опережения", "text": "Отрегулировать угол опережения впрыскивания топлива (п. 5.12.1)"}],
                "spare_parts": []}, 1)])
    raise AssertionError("no marks instruction")


def _marks_buttons(a, c, turn):
    return [x["id"] for x in a.answer_actions(c, turn, set())]


def test_marks_mismatch_is_enough_for_adjustment_ticket(make_agent):
    a = make_agent([_ticket_from_5121, msg("Черновик готов")])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    from digital_mechanic.agent.core import Turn
    assert {"marks_mismatch", "marks_match"} <= set(_marks_buttons(a, c, Turn()))
    turn = a.handle(c, {"lang": "ru", "id": "u1"}, "Метки не совпадают")
    instr = str(a.llm.calls[0]["messages"])
    assert "НЕ СОВПАДАЮТ" in instr and "угла не нужно" in instr and "5.12.1" in instr
    assert a.marks_state(c) == "mismatch"
    h = a.store.handoffs(vehicle_id="M333YK47")[0]
    assert h["kind"] == "inspection" and h["payload"]["items"][0]["finding"] == "метки не совпадают" and "angle" not in json.dumps(h["payload"])
    assert c.draft, turn.tools  # the draft is ready without any measured angle
    assert c.draft["requires_inspection"] is False and "метки" in c.draft["inspection"]
    assert c.draft["operations"][0]["page"] == 255 and c.draft["operations"][0]["document"] == "236.01.01 РК"
    assert a.toir.sent == []  # separate confirmation of the ticket
    t2 = a.handle(c, {"lang": "ru", "id": "u1"}, "Подтверждаю заявку")
    assert "TKT-" in t2.text and len(a.toir.sent) == 1 and a.toir.sent[0]["anomaly_type"] == "F08"


def test_marks_match_saved_check_stays_open_no_ticket(make_agent):
    def answer(messages):
        assert "СОВПАЛИ" in str(messages) and "НЕ пиши, что двигатель исправен" in str(messages)
        return msg("Двигатель исправен, проверку можно закрыть.\nДругая причина: износ распылителей форсунок [S1]. Проверить форсунки на стенде [S1].")

    a = make_agent([answer, msg(None, [tool_call("prepare_ticket", {"conclusion": "x", "operations": [], "spare_parts": []}, 2)])])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    turn = a.handle(c, {"lang": "ru", "id": "u1"}, "Метки совпали")
    assert a.marks_state(c) == "match"
    h = a.store.handoffs(vehicle_id="M333YK47")[0]
    assert h["payload"]["marks"] == "match" and h["payload"]["check_open"] is True
    assert "метки угла опережения совпали" in turn.text and "Проверка не закрыта" in turn.text
    assert "исправен" not in turn.text and "можно закрыть" not in turn.text  # never closed as serviceable
    assert "форсун" in turn.text  # next causes / checks from the manual
    assert c.draft is None and a.toir.sent == []
    assert not any(x["id"] in ("create_ticket", "marks_match", "marks_mismatch") for x in turn.actions)
    # the ticket tool refuses in this turn
    c.hold_ticket = True
    assert "error" in a.tool_prepare_ticket(c, {"conclusion": "x", "operations": [], "spare_parts": []})


def test_marks_buttons_only_for_f08(make_agent):
    from digital_mechanic.agent.core import Turn
    a = make_agent([])
    for v in a.fleet.all():
        for s in a.fleet.sessions(v.vehicle_id):
            if s.status.value == "anomaly" and s.anomaly_type != "F08":
                c = a.conv("u1", v.vehicle_id)
                a.diagnose(c, s.session_id)
                assert not {"marks_mismatch", "marks_match"} & set(_marks_buttons(a, c, Turn()))
                return
