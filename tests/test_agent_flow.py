from conftest import msg, tool_call


def first_failed(agent):
    for v in agent.fleet.all():
        for s in agent.fleet.sessions(v.vehicle_id):
            if s.status.value == "check_failed":
                return v.vehicle_id, s.session_id


def test_check_failed_no_diagnosis_no_llm(make_agent):
    a = make_agent([])
    vid, sid = first_failed(a)
    c = a.conv("u1", vid)
    card = a.diagnose(c, sid)
    assert card["status"] == "check_failed" and card["light"] == "repeat"
    turn = a.handle(c, {"lang": "ru"}, "", event="explain_diagnosis")
    assert "Повторите запись" in turn.text and a.llm.calls == []
    res = a.tool_prepare_ticket(c, {"conclusion": "x", "operations": [], "spare_parts": []})
    assert "error" in res


def test_agent_picks_tools_and_ticket_goes_through_gate(make_agent):
    def ticket_call(messages):
        # find the label of page 269 (valve clearance) among search results
        import json
        for m in messages:
            if m.get("role") == "tool":
                for f in json.loads(m["content"]).get("fragments", []):
                    if f["pdf_page"] == 269:
                        return msg(None, [tool_call("prepare_ticket", {"conclusion": "Стук, дефектовка", "operations": [
                            {"text": "Проверить тепловые зазоры", "source": f["label"], "quote": "Проверить и подрегулировать тепловые зазоры клапанного механизма"},
                            {"text": "Выдуманная работа", "source": f["label"], "quote": "этого текста на странице нет вообще"}], "spare_parts": []}, 2)])
        raise AssertionError("page 269 not retrieved")

    a = make_agent([
        msg(None, [tool_call("search_manual", {"query": "регулировка тепловых зазоров клапанов"}, 1)]),
        ticket_call,
        msg("Черновик готов [S1]"),
    ])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    turn = a.handle(c, {"lang": "ru"}, "Создать заявку")
    assert [t["tool"] for t in turn.tools] == ["search_manual", "prepare_ticket"]
    assert c.draft and len(c.draft["operations"]) == 1 and c.draft["rejected"]
    assert any(cd["kind"] == "ticket_draft" for cd in turn.cards)
    assert a.toir.sent == []  # nothing sent before confirmation
    turn2 = a.handle(c, {"lang": "ru"}, "Подтверждаю")
    assert "TKT-" in turn2.text and len(a.toir.sent) == 1
    sent = a.toir.sent[0]
    assert sent["references"][0] == {"manual": "236.01.01 РК", "pdf_page": 269}
    assert set(sent) == {"vehicle_id", "session_id", "anomaly_type", "conclusion", "urgency", "operations", "spare_parts", "references"}


def test_invented_numbers_are_removed(make_agent):
    a = make_agent([
        msg(None, [tool_call("search_manual", {"query": "момент затяжки болтов крышек коренных подшипников"}, 1)]),
        msg("Момент 421-461 Н·м [S1]. Ещё момент 999 Н·м [S1]."),
        msg("Момент 421-461 Н·м [S1]. Ещё момент 999 Н·м [S1]."),  # fix attempt keeps the bad number
    ])
    c = a.conv("u1", "M333YK47")
    turn = a.handle(c, {"lang": "ru"}, "Какой момент затяжки крышек коренных подшипников?")
    assert "421-461" in turn.text and "999" not in turn.text
    assert turn.sources and turn.sources[0]["document"] in ("236.01.01 РК", "236-3902150-Б РЭ")


def test_repair_session_voice_next(make_agent):
    def start(messages):
        import json
        for m in messages:
            if m.get("role") == "tool":
                f = json.loads(m["content"])["fragments"][0]
                q = f["text"].split("\n")[0][:40]
                return msg(None, [tool_call("start_repair", {"title": "Регулировка", "steps": [
                    {"text": "Шаг А", "source": f["label"], "quote": q}, {"text": "Шаг Б", "source": f["label"], "quote": q}]}, 2)])
    a = make_agent([msg(None, [tool_call("search_manual", {"query": "регулировка тепловых зазоров"}, 1)]), start, msg("План готов")])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    t = a.handle(c, {"lang": "ru"}, "Начать ремонт")
    assert any(cd["kind"] == "repair" for cd in t.cards)
    t2 = a.handle(c, {"lang": "ru"}, "готово")
    assert t2.text.startswith("Шаг 2 из 2")
    t3 = a.handle(c, {"lang": "ru"}, "дальше")
    assert "завершён" in t3.text
    assert a.store.repair_open("M333YK47") is None


def test_history_scoped_to_current_check(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    ctx = a._context(c)
    later = [s.session_id for s in a.fleet.sessions("M333YK47")]
    later = later[later.index("S-0005") + 1:]
    for sid in later:
        assert sid not in ctx.split("Проверки этой машины до текущей включительно:")[1].split("\n")[0]


def test_manager_tools_only_for_manager(make_agent):
    a = make_agent([msg("ok")])
    c = a.conv("m1", None)
    a.handle(c, {"lang": "ru", "role": "manager"}, "Сколько машин в карантине?")
    assert a.llm.calls[0]["tools"] == ["fleet_overview", "get_vehicle_history"]
