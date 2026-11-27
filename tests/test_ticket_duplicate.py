"""Audit 27.11 10:30 (Vercel): the C1 explanation said «заявка TKT-0010 уже в ТОиР», then «Создай заявку на дефектовку» made
TKT-0011 for the same check. A second ticket for the same check and fault is refused with the existing number, unless the
mechanic asks for a NEW ticket; a ticket the chief returned does not block a new one."""


def _draft(a, c):
    res = a.tool_search_manual(c, "регулировка тепловых зазоров клапанов")
    lab = next(x["label"] for x in res["fragments"] if x["pdf_page"] == 269)
    return a.tool_prepare_ticket(c, {"conclusion": "x", "operations": [{"source": lab, "quote": "Проверить и подрегулировать тепловые зазоры клапанного механизма"}]})


def test_second_ticket_for_same_check_refused(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    assert _draft(a, c).get("draft_ready")
    tid = a.confirm(c, None, True)["ticket_id"]
    r = _draft(a, c)
    assert "error" in r and r["existing_ticket"] == tid and tid in r["error"] and c.draft is None
    c.ticket_again = True  # «создай новую заявку»
    assert _draft(a, c).get("draft_ready")


def test_returned_ticket_does_not_block(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    _draft(a, c)
    tid = a.confirm(c, None, True)["ticket_id"]
    a.store.set_ticket_review(tid, {"decision": "returned"}, "returned")
    assert _draft(a, c).get("draft_ready")


def test_new_ticket_phrase_detected():
    import re
    from digital_mechanic.agent import core
    src = open(core.__file__, encoding="utf-8").read()
    pat = re.search(r'c\.ticket_again = bool\(re\.search\(r"(.+?)", raw\)\)', src).group(1)
    for yes in ("Создай новую заявку", "нужна ещё одна заявка", "оформи отдельную заявку на ремонт ТНВД", "повторная заявка"):
        assert re.search(pat, yes), yes
    for no in ("Создай заявку на дефектовку", "Создать заявку на дефектовку", "какая заявка в ТОиР?"):
        assert not re.search(pat, no), no
