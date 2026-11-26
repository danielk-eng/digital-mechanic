"""Iteration 26.11: symptom questions ("почему стучит двигатель") reach the fault tables; prefetch before the LLM."""
from conftest import msg


def test_verb_forms_share_stem_with_manual_nouns():
    from digital_mechanic.retrieval.bm25 import stem
    assert stem("стучит") == stem("стук") == stem("стуки")
    assert stem("вибрирует") == stem("вибрация")


def test_symptom_expansion_word_boundaries():
    from digital_mechanic.retrieval.expand import expand
    qs, sym = expand("Почему стучит двигатель?")
    assert sym and len(qs) == 3 and "стук" in qs[1]
    qs, sym = expand("Как отрегулировать зазоры в клапанах?")  # "отреГУЛировать" must not trigger the "гул" symptom
    assert len(qs) == 1 and not sym


def test_knocking_question_finds_fault_table(make_agent):
    a = make_agent([])
    c = a.conv("u1", "M333YK47")  # ЯМЗ-236М2
    r = a.tool_search_manual(c, "почему стучит двигатель")
    texts = " ".join(f["text"] for f in r["fragments"])
    assert "Двигатель стучит" in texts or "стук" in texts.lower()
    assert any(f["document"] == "236-3902150-Б РЭ" and f["pdf_page"] in (53, 54) for f in r["fragments"])


def test_warm_oil_pressure_page_found(index):
    from digital_mechanic.retrieval.expand import expand
    qs, _ = expand("Какое давление масла должно быть на прогретом двигателе?")
    hits = index.search_multi("ЯМЗ-236М2", qs, k=6)
    assert any(h.page.doc_id == "YAMZ236-RE" and h.page.page == 19 for h in hits)


def test_prefetch_gives_fragments_to_first_llm_call(make_agent):
    a = make_agent([msg("**Вероятная причина:** ранний впрыск [S1]")])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    t = a.handle(c, {"lang": "ru"}, "Почему стучит двигатель?")
    first = a.llm.calls[0]["messages"][-1]["content"]
    assert "ФРАГМЕНТЫ РУКОВОДСТВА" in first and "[S1]" in first
    assert len(a.llm.calls) == 1 and t.sources  # answered in one LLM round with a citation
    assert t.tools[0]["tool"] == "search_manual" and t.tools[0]["auto"]


def test_no_prefetch_for_actions(make_agent):
    a = make_agent([msg("ок")])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")
    a.handle(c, {"lang": "ru"}, "Покажи историю машины")
    assert "ФРАГМЕНТЫ РУКОВОДСТВА" not in a.llm.calls[0]["messages"][-1]["content"]


def test_ticket_request_prefetches_fault_procedures(make_agent):
    a = make_agent([msg("ок")])
    c = a.conv("u1", "M333YK47")
    a.diagnose(c, "S-0005")  # anomaly F08
    a.handle(c, {"lang": "ru"}, "Создай заявку")
    first = a.llm.calls[0]["messages"][-1]["content"]
    assert "ФРАГМЕНТЫ РУКОВОДСТВА" in first and "[S1]" in first
