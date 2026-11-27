"""Handoff summary with an unverified number: sentences are dropped one by one, but «стр. 53 [S1] и стр. 52 [S4]» is not split
at the abbreviation (the chief saw «53 [S1] и стр.» and «52 [S4].» as separate lines, Vercel 26.11 22:30)."""
from types import SimpleNamespace

import digital_mechanic.agent.handoff as H
from digital_mechanic.agent.handoff import _sentences


def test_abbreviation_and_page_numbers_stay_in_one_sentence():
    s = "Создана заявка: 5 работ, 2 запчасти (стр. 53 [S1] и стр. 52 [S4]). Оценка 0,76. Итог см. рис. 5 на с. 12."
    assert [x.strip() for x in _sentences(s)] == [
        "Создана заявка: 5 работ, 2 запчасти (стр. 53 [S1] и стр. 52 [S4]).", "Оценка 0,76.", "Итог см. рис. 5 на с. 12."]


def test_summary_drops_only_the_sentence_with_the_bad_number(monkeypatch):
    llm_text = ("**Машина**: МАЗ-5337.\n**Что сделано**: создана заявка, 5 работ, 2 запчасти (стр. 53 [S1] и стр. 52 [S4]). "
                "Давление 9,99 МПа.\n**Что осталось**: ремонт не начат.")
    pkg = {"check": {}, "vehicle": {"vehicle_id": "M333YK47"}, "dialog": [{"text": "q"}], "pages": [], "ticket": None, "draft": None, "repair": None}
    monkeypatch.setattr(H, "package", lambda agent, c: pkg)
    monkeypatch.setattr(H, "human_check", lambda ch: {})
    monkeypatch.setattr(H, "unverified_numbers", lambda text, ev, allowed=None, free_max=0: ["9,99"])
    agent = SimpleNamespace(llm=SimpleNamespace(available=True, chat=lambda *a, **k: (SimpleNamespace(content=llm_text), None)))
    out = H.summarize(agent, SimpleNamespace(frags={}))["text"]
    assert out.split("\n") == ["**Машина**: МАЗ-5337.", "**Что сделано**: создана заявка, 5 работ, 2 запчасти (стр. 53 [S1] и стр. 52 [S4]).",
                               "**Что осталось**: ремонт не начат."]
