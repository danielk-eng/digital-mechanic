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
    assert [t["tool"] for t in turn.tools if not t.get("auto")] == ["search_manual", "prepare_ticket"]
    assert turn.tools[0]["auto"]  # procedures for the current fault prefetched for the ticket
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
    hist = ctx.split("ИСТОРИЯ МАШИНЫ")[1]
    assert "S-0005" in hist
    for sid in later:
        assert sid not in hist


def test_manager_tools_only_for_manager(make_agent):
    a = make_agent([msg("ok")])
    c = a.conv("m1", None)
    a.handle(c, {"lang": "ru", "role": "manager"}, "Сколько машин в карантине?")
    assert a.llm.calls[0]["tools"] == ["fleet_overview", "get_vehicle_history"]


def test_expertise_and_detail_change_prompt_not_grounding(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    novice = a._system(c, {"lang": "ru", "experience": "novice"})
    expert = a._system(c, {"lang": "ru", "experience": "expert", "detail": "detailed"})
    assert "новичок" in novice and "эксперт" in expert and "до 25 строк" in expert and "не больше 6 строк" in novice
    for s in (novice, expert):
        assert "ТОЛЬКО из фрагментов" in s  # same grounding rule in every variant


def test_expand_reuses_fragments_and_verifies_numbers(make_agent):
    a = make_agent([
        msg(None, [tool_call("search_manual", {"query": "момент затяжки болтов крышек коренных подшипников"}, 1)]),
        msg("421-461 Н·м [S1]."),
        msg("Подробно: момент 421-461 Н·м [S1]. Выдумка 777 Н·м [S1]."),
        msg("Подробно: момент 421-461 Н·м [S1]. Выдумка 777 Н·м [S1]."),
    ])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    short = a.handle(c, {"lang": "ru"}, "Момент затяжки коренных?")
    n_frags = len(c.frags)
    long = a.handle(c, {"lang": "ru"}, short.text, event="expand")
    assert a.llm.calls[2]["tools"] == []  # no new search: same fragments
    assert len(c.frags) == n_frags and "421" in long.text and "777" not in long.text
    assert long.sources and long.sources[0]["page"] == short.sources[0]["page"]


def test_expand_without_citations(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    t = a.handle(c, {"lang": "ru"}, "просто текст", event="expand")
    assert "Нечего развернуть" in t.text and a.llm.calls == []


def _chips(a, c, exp="experienced", lang="ru"):
    return a.suggestions(c, {"lang": lang, "experience": exp})


def test_chips_adapt_to_engine_and_fault(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")  # F08 diesel fuel system, YaMZ-236M2
    chips = _chips(a, c)
    texts = [ch["text"] for ch in chips]
    assert 3 <= len(chips) <= 5
    assert not any("своим ходом" in t for t in texts)  # no generic chips
    manual = [ch for ch in chips if ch["kind"] == "manual"]
    assert manual and all(ch["doc"].startswith("YAMZ236") for ch in manual)
    assert any("впрыск" in t.lower() or "форсун" in t.lower() for t in texts)
    assert any("заявк" in t.lower() for t in texts)


def test_chips_expert_terse_with_pages_and_check_failed(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    expert = [ch for ch in _chips(a, c, "expert") if ch["kind"] == "manual"]
    assert expert and all("стр." in ch["text"] for ch in expert)
    vid, sid = first_failed(a)
    c2 = a.conv("u1", vid)
    a.diagnose(c2, sid)
    t2 = [ch["text"] for ch in _chips(a, c2)]
    assert any("перезапис" in t for t in t2) and not any(ch["kind"] == "manual" for ch in _chips(a, c2))


def test_grounding_filter_drops_topics_missing_in_manual(index):
    from digital_mechanic.agent.suggest import grounded_page
    assert grounded_page(index, "ЯМЗ-236М2", "угол опережения впрыскивания топлива установка")
    assert grounded_page(index, "ЯМЗ-236М2", "свечи зажигания зазор между электродами") is None  # diesel: no spark plugs


def test_alternatives_and_hypothesis_events(make_agent):
    a = make_agent([msg("Других причин в руководстве нет."), msg("В руководстве этого нет.")])
    c = a.conv("u1", "M333YK47")
    a.handle(c, {"lang": "ru"}, "", event="alternatives")
    assert "Другие варианты" in str(a.llm.calls[-1]["messages"])
    a.handle(c, {"lang": "ru"}, "прокладка ГБЦ", event="hypothesis")
    assert "Проверь гипотезу механика: «прокладка ГБЦ»" in str(a.llm.calls[-1]["messages"])


def test_start_chips_and_bare_fault_code(make_agent):
    from digital_mechanic.agent import suggest
    a = make_agent([msg("Код F02 — низкое давление масла.")])
    c = a.conv("u1", "M333YK47")
    chips = suggest.build(a.index, c.vehicle, None, [], None, None, False)
    kinds = [ch.kind for ch in chips]
    assert "code" in kinds and kinds.count("symptom") >= 2
    a.handle(c, {"lang": "ru"}, "F02")
    assert "Код неисправности F02 «Низкое давление масла»" in str(a.llm.calls[-1]["messages"])


def test_summary_handoff_and_ticket_package(make_agent):
    a = make_agent([msg("**Машина**: M333YK47\n**Что сделано**: момент 421-461 Н·м.\n**Что нашли**: выдуманные 777 Н·м.")])
    a.llm.available = lambda: True
    c = a.conv("u1", "M333YK47")
    a.store.upsert_user("u1", name="Иван Петров", role="mechanic")
    a.store.add_message(c.cid, "u1", "M333YK47", "user", "Какой момент?")
    a.store.add_message(c.cid, "u1", "M333YK47", "assistant", "421-461 Н·м")
    r = a.handoff(c, {"id": "u1", "lang": "ru"}, "застрял на шаге 3")
    assert "421-461" in r["summary"] and "777" not in r["summary"]
    h = a.store.handoffs("open")[0]
    assert h["vehicle_id"] == "M333YK47" and h["payload"]["dialog"]
    a.store.delete_user_data("u1")
    assert not a.store.handoffs()


def test_triage_orders_by_urgency(make_agent):
    a = make_agent([msg("F03: проверить термостат [S1]. Время в книге не указано.")])
    order = a.triage("B332BP98")
    assert [o["code"] for o in order][:1] == ["F03"] and len(order) == 2  # overheating before the belt drive
    c = a.conv("u1", "B332BP98")
    t = a.handle(c, {"lang": "ru"}, "", event="triage")
    assert t.text.startswith("**Порядок работ по приоритету**") and "Порядок не меняй" in str(a.llm.calls[-1]["messages"])


def test_inspection_draft_no_new_numbers(make_agent):
    from digital_mechanic.inspection import draft
    a = make_agent([msg('{"items":[{"component":"Ремень генератора","finding":"трещины, прогиб 25 мм","severity":"дефект","source":"фото"}]}')])
    c = a.conv("u1", "M333YK47")
    a.store.add_message(c.cid, "u1", "M333YK47", "assistant", "ok", {"photo": "p.jpg", "photo_desc": "Ремень привода с трещинами"})
    r = draft(a, c)
    assert r["items"] and "25" not in r["items"][0]["finding"] and r["photos"] == ["p.jpg"]


def test_llm_hedged_request_takes_first_answer():
    import threading, time
    from types import SimpleNamespace
    from digital_mechanic.agent.llm import Llm
    from digital_mechanic.config import Settings
    calls = []

    def create(**kw):
        calls.append(1)
        if len(calls) == 1:
            time.sleep(1.5)  # the first request hangs
            return "slow"
        return "fast"
    llm = Llm(Settings(llm_api_key="x", llm_hedge_s=0.2))
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    t = time.time()
    assert llm._create({}) == "fast" and time.time() - t < 1.0 and len(calls) == 2
    llm2 = Llm(Settings(llm_api_key="x", llm_hedge_s=0))
    llm2._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: "one")))
    assert llm2._create({}) == "one"
