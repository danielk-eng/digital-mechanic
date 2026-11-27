import re
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
    # no third LLM round to retell the card: the text is built from the verified draft, with its page label
    assert len(a.llm.calls) == 2 and turn.text.startswith("Черновик заявки на дефектовку готов: 1 работа")
    assert "Приоритет —" in turn.text and "Основание:" in turn.text and "[S" in turn.text and turn.sources
    # the level's meaning once, no «итог: …» retelling of the card's priority block (27.11)
    assert "итог:" not in turn.text and "дефектовка" in turn.text.split("Приоритет —")[1].split("Основание:")[0]
    # page chips on a short line of their own (390 px: they wrapped under the long first line)
    assert turn.text.splitlines()[0].endswith(".") and "[S" not in turn.text.splitlines()[0]
    assert re.match(r"Взято из руководства: (\[S\d+\] ?)+$", turn.text.splitlines()[1])
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
    assert "новичок" in novice and "эксперт" in expert and "до 14 строк" in expert and "3–4 строки" in novice
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


def test_expand_is_prefetched_after_brief_answer(make_agent):
    a = make_agent([
        msg(None, [tool_call("search_manual", {"query": "момент затяжки болтов крышек коренных подшипников"}, 1)]),
        msg("421-461 Н·м [S1]."),
        msg("Подробно: момент 421-461 Н·м [S1]. Выдумка 777 Н·м [S1]."),
        msg("Подробно: момент 421-461 Н·м [S1]. Выдумка 777 Н·м [S1]."),
    ])
    a.expand_prefetch = True
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    short = a.handle(c, {"lang": "ru", "role": "mechanic"}, "Момент затяжки коренных?")
    pf = a._prefetch[c.cid]
    assert pf["done"].wait(10) and pf["ok"]
    n_calls = len(a.llm.calls)
    long = a.handle(c, {"lang": "ru", "role": "mechanic"}, short.text, event="expand")
    assert len(a.llm.calls) == n_calls  # the click reused the background run
    assert "421" in long.text and "777" not in long.text and long.sources
    assert any(t.get("tool") == "expand_prefetch" for t in long.tools)
    assert c.cid not in a._prefetch  # used once
    # a different answer does not take a stale prefetch
    a.prefetch_expand(c, {"lang": "ru"}, short.text)
    a._prefetch[c.cid]["done"].wait(10)
    assert a._prefetch_take(c, "совсем другой ответ про форсунки [S1]", ["S1"]) is None


def test_expand_on_another_instance_rehydrates_sources(make_agent):
    """Vercel: the expand request may hit a fresh instance with an empty conversation -> pages re-read from the index."""
    a = make_agent([
        msg(None, [tool_call("search_manual", {"query": "момент затяжки болтов крышек коренных подшипников"}, 1)]),
        msg("421-461 Н·м [S1]."),
    ])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    short = a.handle(c, {"lang": "ru"}, "Момент затяжки коренных?")
    src = [{"label": s["label"], "doc_id": s["doc_id"], "page": s["page"]} for s in short.sources]
    b = make_agent([msg("Подробно: момент 421-461 Н·м [S1]. Выдумка 777 Н·м [S1]."), msg("Подробно: момент 421-461 Н·м [S1].")])
    c2 = b.conv("u1", "M333YK47")
    assert not c2.frags
    long = b.handle(c2, {"lang": "ru"}, short.text, event="expand", sources=src + [{"label": "S9", "doc_id": "nope", "page": 1}, {"label": "evil"}])
    assert "Нечего развернуть" not in long.text and "421" in long.text and "777" not in long.text
    assert long.sources and long.sources[0]["page"] == short.sources[0]["page"] and set(c2.frags) == {"S1"}
    assert c2.frags["S1"]["text"] == c.frags["S1"]["text"]  # text from our index, not from the client


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


def test_expand_uses_slim_prompt_and_live_lines_skip_unverified_numbers(make_agent):
    seen = {}

    def expand_reply(messages):
        # the streamed draft only shows complete lines whose numbers are in the fragments
        cb = seen["cb"]
        cb("Момент 421-461 Н·м [S1].\nВыдумка 777 Н·м [S1].\nнедописан")
        return msg("Подробно: момент 421-461 Н·м [S1].")
    a = make_agent([
        msg(None, [tool_call("search_manual", {"query": "момент затяжки болтов крышек коренных подшипников"}, 1)]),
        msg("421-461 Н·м [S1]."),
        expand_reply,
    ])
    orig = a.llm.chat

    def chat(step, messages, tools=None, **kw):
        seen.setdefault("kw", {})[step] = kw
        if kw.get("on_text"):
            seen["cb"] = kw["on_text"]
        return orig(step, messages, tools=tools, **kw)
    a.llm.chat = chat
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    short = a.handle(c, {"lang": "ru"}, "Момент затяжки коренных?")
    long = a.handle(c, {"lang": "ru"}, short.text, event="expand")
    sys_prompt = a.llm.calls[2]["messages"][0]["content"]
    assert "ИНСТРУМЕНТЫ" not in sys_prompt and "ИСТОРИЯ МАШИНЫ" not in sys_prompt and len(sys_prompt) < 1500
    assert seen["kw"]["expand"]["max_tokens"] <= 1500
    d = a.drafts[c.cid]
    assert "421-461" in d and "777" not in d and "недописан" not in d and "[S1]" not in d
    assert "421" in long.text


def test_brief_rule_is_compact():
    from digital_mechanic.agent.prompts import DETAIL
    assert "3–4 строки" in DETAIL["brief"] and "**Причина:**" in DETAIL["brief"] and "Подробнее" in DETAIL["brief"]


def test_llm_stream_race_hedges_on_first_token():
    import time
    from types import SimpleNamespace as NS
    from digital_mechanic.agent.llm import Llm
    from digital_mechanic.config import Settings
    calls = []

    def chunk(content=None, tc=None):
        return NS(usage=None, choices=[NS(delta=NS(content=content, tool_calls=tc, reasoning_content=None))])

    def create(**kw):
        calls.append(kw)
        n = len(calls)

        def gen():
            if n == 1:
                time.sleep(2.0)  # queued at the provider: no first token
                yield chunk("slow")
                return
            yield chunk("бы")
            yield chunk("стро")
            yield chunk(None, [NS(index=0, id="c1", function=NS(name="search_manual", arguments='{"query":'))])
            yield chunk(None, [NS(index=0, id=None, function=NS(name=None, arguments=' "x"}'))])
        return gen()
    llm = Llm(Settings(llm_api_key="x", llm_hedge_ttft_s=0.5, llm_stream=True))
    llm._client = NS(chat=NS(completions=NS(create=create)))
    texts = []
    t = time.time()
    m, _ = llm.chat("s", [{"role": "user", "content": "q"}], on_text=texts.append)
    assert time.time() - t < 1.8 and len(calls) == 2 and calls[0]["stream"] is True
    assert m.content == "быстро" and m.tool_calls[0].function.name == "search_manual" and m.tool_calls[0].function.arguments == '{"query": "x"}'
    assert texts and texts[-1] == "быстро"


def test_explain_on_another_instance_restores_the_check(make_agent):
    """Vercel: /api/diagnose ran on instance A, «explain» lands on B whose conversation has no check.
    B re-reads the check the client names (session_id) instead of answering «данных не нашёл»."""
    a = make_agent([msg("Вердикт: отклонение [S1]")])
    v = next(v for v in a.fleet.all() if any(s.status.value == "anomaly" for s in a.fleet.sessions(v.vehicle_id)))
    sid = next(s.session_id for s in a.fleet.sessions(v.vehicle_id) if s.status.value == "anomaly")
    c = a.conv("u-other-instance", v.vehicle_id)
    assert c.check is None
    turn = a.handle(c, {"lang": "ru"}, "", event="explain_diagnosis", session_id=sid)
    assert c.check is not None and c.check.session_id == sid
    assert "данных не нашёл" not in turn.text
    sent = a.llm.calls[0]
    assert "результат текущей проверки" in str(sent)


def test_restore_check_keeps_a_newer_local_check(make_agent):
    a = make_agent([])
    vid, sid = first_failed(a)
    c = a.conv("u2", vid)
    a.diagnose(c, sid)
    assert a.restore_check(c, "S-OTHER") is False and c.check.session_id == sid


def test_citation_extract_drops_absence_claims():
    from digital_mechanic.agent.core import _drop_absence_claims as d
    out = d("В разделе коды ошибок, включая F08, не упоминаются. При воздухе в системе — прокачать систему [с. 52].")
    assert "не упоминаются" not in out and "прокачать систему" in out
    assert d("- прокачать систему\n- код F08 в разделе не упоминается\n- заменить насос") == "- прокачать систему\n- заменить насос"
    assert d("Момент затяжки указан только для стартера.") == "Момент затяжки указан только для стартера."


def test_ticket_draft_token_roundtrip_and_tamper():
    from digital_mechanic.web.app import draft_from_token, draft_token
    d = {"id": "abc12345", "request": {"vehicle_id": "M333YK47", "session_id": "S-0005"}, "operations": []}
    tok = draft_token("u1", "M333YK47", d)
    assert draft_from_token("u1", "M333YK47", tok) == d
    assert draft_from_token("u2", "M333YK47", tok) is None  # another user
    assert draft_from_token("u1", "A464EP47", tok) is None  # another vehicle
    b, sig = tok.rsplit(".", 1)
    assert draft_from_token("u1", "M333YK47", b[:-2] + "xx." + sig) is None  # edited draft


def test_llm_stalled_stream_is_dropped_and_retried():
    """Vercel at night: the free model started streaming, then went silent (one answer took 107 s).
    A winner silent for `llm_stall_s` is cancelled and the request is sent again."""
    import time
    from types import SimpleNamespace as NS
    from digital_mechanic.agent.llm import Llm
    from digital_mechanic.config import Settings
    calls = []

    def chunk(content):
        return NS(usage=None, choices=[NS(delta=NS(content=content, tool_calls=None, reasoning_content=None))])

    def create(**kw):
        calls.append(kw)
        n = len(calls)

        def gen():
            yield chunk("нача")
            if n == 1:
                time.sleep(5)  # stalls after the first token
            yield chunk("ло")
        return gen()
    llm = Llm(Settings(llm_api_key="x", llm_hedge_ttft_s=3, llm_stall_s=0.6, llm_stream=True))
    llm._client = NS(chat=NS(completions=NS(create=create)))
    t = time.time()
    m, _ = llm.chat("s", [{"role": "user", "content": "q"}])
    assert m.content == "начало" and len(calls) == 2 and time.time() - t < 4


def test_llm_turn_budget_stops_calls():
    """A turn never waits past `llm_turn_budget_s`: no model call starts after the deadline (the agent then shows pages)."""
    import time
    from types import SimpleNamespace as NS
    import pytest
    from digital_mechanic.agent.llm import Llm, LlmUnavailable, REQUEST_DEADLINE
    from digital_mechanic.config import Settings
    llm = Llm(Settings(llm_api_key="x", llm_stream=True, llm_hedge_ttft_s=10))
    calls = []

    def create(**kw):
        calls.append(1)

        def gen():
            time.sleep(8)
            yield None
        return gen()
    llm._client = NS(chat=NS(completions=NS(create=create)))
    tok = REQUEST_DEADLINE.set(time.time() + 4.5)
    try:
        t = time.time()
        with pytest.raises(LlmUnavailable):
            llm.chat("s", [{"role": "user", "content": "q"}])
        assert time.time() - t < 5.5  # no second attempt after the budget (was up to 2 × 90 s)
        with pytest.raises(LlmUnavailable):
            llm.chat("s2", [{"role": "user", "content": "q"}])  # budget spent: no call at all
        assert len(calls) == 1
    finally:
        REQUEST_DEADLINE.reset(tok)
    assert REQUEST_DEADLINE.get() is None


def test_facts_pass_streams_verified_facts_as_draft(make_agent):
    """Typed answer on Vercel: the facts pass is most of the wait. Complete facts of the streamed JSON show at once
    (draft «Нашёл в руководстве»); a fact with a figure that is not on the fragments is never shown."""
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    fp = a.fp
    frags = [{"text": "Зазор между коромыслом и клапаном 0,25–0,30 мм на холодном двигателе."}]
    cb = fp._live_facts(c, frags)
    cb('{"answerable": true, "facts": [{"src": "S1", "text": "Зазор 0,25–0,30 мм на холодном"}, {"src": "S1", "text": "Момент 999 Н·м"}, {"src": "S1", "text": "недопис')
    d = a.drafts[c.cid]
    assert d.startswith("Нашёл в руководстве:") and "0,25–0,30 мм" in d and "999" not in d and "недопис" not in d


def test_expand_detached_leaves_conversation_alone(make_agent):
    """Client prefetch of «Подробнее»: runs on a copy (no lock held, no frags/progress/draft written to the dialog)."""
    a = make_agent([
        msg(None, [tool_call("search_manual", {"query": "момент затяжки болтов крышек коренных подшипников"}, 1)]),
        msg("421-461 Н·м [S1]."),
        msg("Подробно: момент 421-461 Н·м [S1]."),
    ])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    short = a.handle(c, {"lang": "ru"}, "Момент затяжки коренных?")
    src = [{"label": s["label"], "doc_id": s["doc_id"], "page": s["page"]} for s in short.sources]
    frags = {k: dict(v) for k, v in c.frags.items()}
    with c.lock:  # the dialog is busy with the next question: the prefetch must not wait for it
        long = a.handle(c, {"lang": "ru"}, short.text, event="expand", sources=src, detached=True)
    assert "421" in long.text and long.sources
    assert c.frags == frags and (c.cid + "#pf") not in a.progress and (c.cid + "#pf") not in a.drafts
