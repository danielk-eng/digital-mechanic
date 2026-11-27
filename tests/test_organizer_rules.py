"""Organizers' official answers (task chat 26.11) and the day-2 briefing (27.11): one test per rule."""
import gzip
import json
import re
from pathlib import Path

from conftest import msg, tool_call
from digital_mechanic import policy
from digital_mechanic.agent import answer_post, applicability, contradictions
from digital_mechanic.agent.grounding import quote_in_text
from digital_mechanic.schemas import PolicyDecision, Urgency

ROOT = Path(__file__).resolve().parents[1]


def _check(a, vid, sid):
    return next(s for s in a.fleet.sessions(vid) if s.session_id == sid), a.fleet.get(vid)


# ---- rule 2 / (c): confidence < 0,50 -> re-record, no ticket; 0,50-0,60 -> inspection on site, ticket after confirm
def test_two_real_048_vehicles_rerecord_no_ticket(make_agent):
    a = make_agent([])
    for vid, sid, code in (("M739BA198", "S-0103", "F01"), ("P227BB78", "S-0117", "F13")):
        ch, v = _check(a, vid, sid)
        assert ch.confidence == 0.48 and ch.anomaly_type == code
        assert policy.decide(ch, v) == PolicyDecision.recheck_low_confidence
        assert not policy.may_create_ticket(ch, v)
        c = a.conv("u1", vid)
        card = a.diagnose(c, sid)
        assert card["light"] == "repeat" and "0,48" in card["light_text"] and "0,50" in card["light_text"] and "повторите запись" in card["light_text"]
        assert card["thresholds"] == {"rerecord": 0.5, "inspect": 0.6} and card["ticket_allowed"] is False
        turn = a.handle(c, {"lang": "ru"}, "Создать заявку на дефектовку")
        assert turn.text.startswith("Заявку не оформляю") and "0,50" in turn.text and a.llm.calls == []
        assert "error" in a.tool_prepare_ticket(c, {"operations": [], "spare_parts": []})
        assert not any(ch_["text"].startswith("Создать заявку") for ch_ in a.suggestions(c, {"lang": "ru"}))


def test_056_inspection_on_site_ticket_after_conscious_confirm(make_agent):
    a = make_agent([])
    ch, v = _check(a, "C991PH47", "S-0148")
    assert ch.confidence == 0.56
    assert policy.decide(ch, v) == PolicyDecision.verify_then_ticket and policy.may_create_ticket(ch, v)
    assert policy.doubt_flags(ch)  # the warning is always shown
    card = a.verdict_card(ch, policy.decide(ch, v), *policy.urgency(ch), v)
    assert card["doubt"] and card["ticket_allowed"]


def test_bad_record_no_conclusions_no_ticket_no_exceptions(make_agent):
    a = make_agent([])
    for v in a.fleet.all():
        for s in a.fleet.sessions(v.vehicle_id):
            if s.signal_quality.value == "bad":
                assert policy.decide(s, v) == PolicyDecision.repeat_recording and not policy.may_create_ticket(s, v)
                assert "повторите запись" in policy.ticket_refusal(s, v)


# ---- rule 7: diagnosis vs vehicle card (Y652CK98 / S-0132, petrol ЗМЗ-5234.10 with F08 diesel fuel supply)
def test_mismatch_card_wins_no_diagnosis_no_ticket(make_agent):
    a = make_agent([])
    ch, v = _check(a, "Y652CK98", "S-0132")
    assert v.engine_model == "ЗМЗ-5234.10" and ch.anomaly_type == "F08"
    assert policy.decide(ch, v) == PolicyDecision.diagnosis_mismatch and not policy.may_create_ticket(ch, v)
    c = a.conv("u1", "Y652CK98")
    card = a.diagnose(c, "S-0132")
    txt = card["light_text"]
    assert "ошибка модели" in txt and "Карточка верна" in txt and "не оформляется" in txt and "Повторите проверку или проведите дефектовку" in txt
    assert "urgency" not in card  # no diagnosis -> no priority
    turn = a.handle(c, {"lang": "ru"}, "Создать заявку")
    assert turn.text.startswith("Заявку не оформляю") and a.llm.calls == []
    assert "РАСХОЖДЕНИЕ С КАРТОЧКОЙ" in a._context(c)
    # the generalisation: every fault type that cannot occur on the card's fuel
    for code, fuel in policy.FAULT_FUEL.items():
        other = "бензин" if fuel == "дизель" else "дизель"
        veh = next(x for x in a.fleet.all() if x.fuel == other)
        fake = ch.model_copy(update={"anomaly_type": code})
        assert policy.mismatch(fake, veh)
    same = ch.model_copy(update={"anomaly_type": "F12"})
    assert policy.mismatch(same, v) is None


# ---- rule 3: priority meanings, bounded adjustment, mechanic's final choice, ban only with a manual page
def test_priority_meanings_and_bounded_adjustment(make_agent):
    a = make_agent([])
    assert "дефектовка до выпуска в рейс" in policy.LIGHT[Urgency.high][1] and "не выпускать" not in policy.LIGHT[Urgency.high][1]
    assert "ближайшем ТО" in policy.LIGHT[Urgency.medium][1] and "под наблюдением" in policy.LIGHT[Urgency.medium][1]
    assert "следующей проверке" in policy.LIGHT[Urgency.low][1]
    ch, _ = _check(a, "M333YK47", "S-0005")
    for score in (0.1, 0.5, 0.79, 0.8, 0.99):
        for conf in (0.5, 0.7, 0.95):
            for code, cls in policy.FAULT_SEVERITY.items():
                u, why = policy.urgency(ch.model_copy(update={"anomaly_type": code, "anomaly_score": score, "confidence": conf}))
                lvl = {"low": 0, "medium": 1, "high": 2}[u.value]
                assert abs(lvl - policy.BASE_LEVEL[cls[0]]) <= 1 and "механик" in why[-1]


def test_prohibition_only_with_explicit_manual_page():
    assert policy.prohibition({"S1": {"text": "Проверить давление масла манометром.", "page": 5, "document": "РЭ"}}) is None
    ban = policy.prohibition({"S1": {"text": "При давлении ниже 100 кПа эксплуатация двигателя запрещается.", "page": 19, "document": "РЭ", "doc_id": "X"}})
    assert ban and ban["page"] == 19 and "запрещается" in ban["quote"]
    assert policy.prohibition({"X1": {"text": "эксплуатация запрещается", "extra": True, "page": 1}}) is None


def test_mechanic_changes_priority_on_confirm(make_agent):
    import test_agent_flow as f
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    res = a.tool_search_manual(c, "регулировка тепловых зазоров клапанов")
    lab = next(x["label"] for x in res["fragments"] if x["pdf_page"] == 269)
    r = a.tool_prepare_ticket(c, {"conclusion": "x", "operations": [{"source": lab, "quote": "Проверить и подрегулировать тепловые зазоры клапанного механизма"}]})
    assert r.get("draft_ready") and c.draft["urgency_options"]["high"].startswith("высокий")
    out = a.confirm(c, None, True, "low")
    assert out["ticket_id"] and a.toir.sent[-1]["urgency"] == "low"


# ---- rule 4 / (d): applicable procedure, evidence, repair-only ticket
def test_applicability_rules():
    toc = {"text": "Содержание\n1 Общие сведения ........ 5\n2 Разборка ........ 12\n3 Ремонт ........ 40\n4 Сборка ........ 80\n5 Испытания ........ 90"}
    assert applicability.is_toc(toc["text"])
    ops = [{"label": "S1", "text": "Заменить ремень", "page": 3}]
    kept, errs = applicability.repair_ops(ops, "F04", {"S1": toc})
    assert kept == [] and "оглавление" in errs[0]
    other = {"text": "Отрегулировать угол опережения впрыска топлива."}
    kept, errs = applicability.repair_ops([{"label": "S1", "text": "Отрегулировать угол", "page": 9}], "F05", {"S1": other})
    assert kept == [] and "не относится к узлу" in errs[0]
    insp = {"text": "Осмотреть ремень привода генератора на наличие трещин."}
    kept, errs = applicability.repair_ops([{"label": "S1", "text": "Осмотреть ремень", "quote": "Осмотреть ремень привода", "page": 9}], "F04", {"S1": insp})
    assert kept == [] and "только на ремонт" in errs[-1]
    ok = {"text": "Заменить ремень привода генератора, натянуть ремень."}
    kept, _ = applicability.repair_ops([{"label": "S1", "text": "Заменить ремень", "quote": "Заменить ремень", "page": 9}], "F04", {"S1": ok})
    assert len(kept) == 1


def test_low_oil_pressure_needs_manometer_page_with_norm():
    repair = {"text": "Заменить масляный насос, промыть масляный фильтр."}
    kept, errs = applicability.repair_ops([{"label": "S1", "text": "Заменить масляный насос", "page": 50}], "F02", {"S1": repair})
    assert kept == [] and "манометр" in errs[-1]
    mano = {"text": "Проверить давление масла контрольным манометром. Давление масла должно быть 400-700 кПа (4-7 кгс/см2)."}
    kept, _ = applicability.repair_ops([{"label": "S1", "text": "Заменить масляный насос", "page": 50}], "F02", {"S1": repair, "S2": mano})
    assert len(kept) == 1 and applicability.oil_pressure_evidence({"S2": mano})["label"] == "S2"


def test_no_applicable_instruction_no_ticket_but_recommendation(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    c.frags["S99"] = {"text": "Содержание\n1 ........ 5\n2 ........ 9\n3 ........ 12\n4 ........ 14", "document": "236.01.01 РК", "doc_id": "YAMZ236-RK", "page": 3}
    r = a.tool_prepare_ticket(c, {"operations": [{"source": "S99", "quote": "Содержание 1 ........ 5"}]})
    assert "error" in r and "нет применимой инструкции" in r["error"]
    rec = r["defect_recommendation"]
    assert "Рекомендация на дефектовку" in rec and "M333YK47" in rec and "S-0005" in rec and "нет инструкции по найденной неисправности" in rec
    assert c.draft is None and a.toir.sent == []


def test_no_inspection_in_ticket_hint():
    src = (ROOT / "src/digital_mechanic/agent/core.py").read_text(encoding="utf-8")
    assert "общую проверку/осмотр узла и повтори" not in src


# ---- rule 5: hypotheses labelled, numbers only with a page
def test_uncited_numbers_and_unlabelled_quotes_removed():
    txt = ("**Наблюдения:** стук в клапанном механизме [S1].\n"
           "Гипотеза: износ коромысла, зазор вырос до 0,6 мм.\n"
           "Зазор 0,25-0,30 мм [S1].\n"
           "Момент 43 кгс·м.\n"
           "«Регулировать зазоры на холодном двигателе обязательно всегда»")
    out, removed = answer_post.guard_uncited(txt)
    assert "0,25-0,30 мм [S1]" in out and "0,6 мм" not in out and "43 кгс" not in out and "холодном" not in out
    assert len(removed) == 3 and "Гипотеза" not in out or "0,6" not in out


# ---- rule 6: contradictions
def test_contradictions_quotes_verbatim_in_index():
    pages = {}
    for line in gzip.open(ROOT / "data/index/pages.jsonl.gz", "rt"):
        r = json.loads(line)
        pages[(r["doc_id"], r["page"])] = r["text"]
    for k in contradictions.KNOWN:
        for s in k["sides"]:
            assert quote_in_text(s["quote"], pages[(s["doc_id"], s["page"])]), (k["id"], s["page"])


def test_contradiction_cases_and_bolt_resolution(make_agent):
    assert [k["id"] for k in contradictions.find("ЯМЗ-236М2", "На каком двигателе регулировать тепловые зазоры клапанов?")] == ["yamz236_valve_cold"]
    b = contradictions.find("ЯМЗ-236М2", "Момент затяжки болтов крышек коренных подшипников")
    assert [k["id"] for k in b] == ["yamz236_main_bearing_bolts"]
    assert "три приёма" in b[0]["safe_rule"] and "Сборка двигателя" in b[0]["resolution"] and "с. 248" in b[0]["resolution"]
    o = contradictions.find("ЯМЗ-236М2", "Какое давление масла на прогретом двигателе?")
    assert "400–700" in o[0]["safe_rule"] and {s["page"] for s in o[0]["sides"]} == {19, 267, 12}
    v = contradictions.find("ЯМЗ-236М2", "зазоры клапанов")[0]
    assert "холодном двигателе" in v["safe_rule"] and "не подтверждает" in v["safe_rule"] and {s["page"] for s in v["sides"]} == {31, 269, 276}
    assert contradictions.find("ЗМЗ-5234.10", "зазоры клапанов") == []
    a = make_agent([msg("Противоречие в руководствах: см. карточку. Регулировать на холодном двигателе [S1].")])
    c = a.conv("u1", "M333YK47")
    turn = a.handle(c, {"lang": "ru", "id": "u1"}, "Когда регулировать тепловые зазоры клапанов — на холодном?")
    cd = [x for x in turn.cards if x["kind"] == "contradiction"]
    assert cd and len(cd[0]["sides"]) == 3 and "уточнить у производителя" in cd[0]["clarify"]
    gaps = a.store.handoffs(None)
    assert any(g["kind"] == "kb_gap" and g["payload"]["gap"] == "contradiction" for g in gaps)


# ---- rule 8 / (a) / (f): no «обратитесь к специалисту», concrete action, chief notified
def test_no_specialist_phrase_anywhere():
    bad = re.compile(r"(обрат\w+|рекоменд\w+ дефектовку)\s+(к|у)\s+специалист", re.I)
    for p in (ROOT / "src").rglob("*"):
        if p.suffix in (".py", ".js", ".json", ".html"):
            for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if bad.search(ln):
                    assert "Не пиши" in ln or "no «обратитесь" in ln or "a concrete action" in ln, (p, ln[:120])
    assert "специалист" not in answer_post.no_specialist("Данных нет. Рекомендую обратиться к специалисту.")


def test_no_data_answer_concrete_action_and_gap_for_chief(make_agent):
    a = make_agent([msg("В руководстве для этого двигателя данных нет. Обратитесь к специалисту.")])
    c = a.conv("u1", "M333YK47")
    turn = a.handle(c, {"lang": "ru", "id": "u1", "name": "Иванов"}, "Какой ресурс у шин Michelin на этой машине?")
    assert "специалист" not in turn.text and "дефектовку" in turn.text
    g = [h for h in a.store.handoffs(None) if h["kind"] == "kb_gap"]
    assert g and g[0]["payload"]["gap"] == "no_data" and g[0]["payload"]["engine"] == "ЯМЗ-236М2" and "Нет данных" in g[0]["summary"]
    from digital_mechanic.agent.core import NO_DATA_TEXT
    assert "специалист" not in NO_DATA_TEXT and "дефектовку" in NO_DATA_TEXT


def test_multiday_repair_resumed_by_another_mechanic(make_agent):
    """Day-2 (g): the repair belongs to the vehicle; the next mechanic sees the step where work stopped."""
    a = make_agent([])
    a.store.upsert_user("m1", name="Иванов А.", role="mechanic")
    steps = [{"text": "Снять крышку головки", "document": "236.01.01 РК", "page": 269, "done": True},
             {"text": "Проверить зазоры щупом", "document": "236.01.01 РК", "page": 269, "done": False},
             {"text": "Отрегулировать зазоры", "document": "236.01.01 РК", "page": 276, "done": False}]
    r = a.store.start_repair("m1", "E549YT198", None, "TKT-0042", "Регулировка клапанов", steps)
    r["current"] = 1
    a.store.save_repair(r)
    note = a.resume_note("E549YT198", {"id": "m2"})
    assert note["kind"] == "repair" and note["step"] == 1
    assert "шаге 2 из 3" in note["text"] and "Иванов А." in note["text"] and "стр. 269" in note["text"]
    assert "Вы начали" in a.resume_note("E549YT198", {"id": "m1"})["text"]
