"""V t564u: automatic fallback DeepSeek (TokenRouter free) -> GigaChat-4 (Cloud.ru free) on failure or degenerate output."""
from types import SimpleNamespace as NS

import pytest

from conftest import msg, tool_call
from digital_mechanic.agent.llm import (REQUEST_CONV, REQUEST_LLM_EVENTS, Llm, LlmUnavailable, degenerate_text)
from digital_mechanic.config import Settings

GOOD = "Момент затяжки болтов крышек коренных подшипников — 421-461 Н·м [S1]."


def reply(text, calls=None):
    return NS(choices=[NS(message=NS(content=text, tool_calls=calls))], usage=NS(prompt_tokens=10, completion_tokens=5))


class Http(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


def make(primary, reserve=lambda **kw: reply("Резерв: " + GOOD), key="k"):
    llm = Llm(Settings(llm_api_key="x", cloudru_api_key=key, llm_primary="tokenrouter", llm_stream=False, llm_hedge_s=0,
                       llm_turn_budget_s=0, comet_api_key=""))
    seen = {"primary": [], "reserve": []}

    def p(**kw):
        seen["primary"].append(kw)
        return primary(**kw)

    def r(**kw):
        seen["reserve"].append(kw)
        return reserve(**kw)
    llm._client = NS(chat=NS(completions=NS(create=p)))
    llm._clients["cloudru"] = NS(chat=NS(completions=NS(create=r)))
    return llm, seen


def run(llm, text="Какой момент?", conv="c1", step="agent_r0"):
    ev = []
    t1, t2 = REQUEST_LLM_EVENTS.set(ev), REQUEST_CONV.set(conv)
    try:
        m, _ = llm.chat(step, [{"role": "user", "content": text}])
    finally:
        REQUEST_LLM_EVENTS.reset(t1); REQUEST_CONV.reset(t2)
    return m, ev


def _raise(e):
    def f(**kw):
        raise e
    return f


@pytest.mark.parametrize("err,why", [(TimeoutError("нет первого токена за 25 с"), "таймаут"), (Http(429), "HTTP 429"), (Http(503), "HTTP 503")])
def test_failure_switches_to_gigachat_once(err, why):
    llm, seen = make(_raise(err))
    m, ev = run(llm)
    assert m.content.startswith("Резерв:")
    assert len(seen["primary"]) == 1  # one attempt on the primary when a reserve exists (latency cap)
    assert seen["reserve"][0]["model"] == "GigaChat/GigaChat-4-708B-A41"  # the free reserve model only
    sw = [e for e in ev if e["tool"] == "llm_fallback"][0]
    assert sw["from"] == "tokenrouter" and sw["to"] == "cloudru" and why in sw["reason"]
    assert any(e["tool"] == "llm_answered" and e["provider"] == "cloudru" for e in ev)


def test_primary_call_has_short_timeout():
    llm, seen = make(lambda **kw: reply(GOOD))
    m, ev = run(llm)
    assert m.content == GOOD and seen["primary"][0]["timeout"] == 25 and ev == []  # no switch, nothing logged


def test_empty_answer_switches():
    llm, _ = make(lambda **kw: reply(""))
    m, ev = run(llm)
    assert m.content.startswith("Резерв:") and ev[0]["reason"] == "пустой ответ"


def test_ngram_loop_inside_answer_switches():
    loop = "Проверьте форсунку и затем снова проверьте форсунку. " * 8
    assert degenerate_text(loop) and degenerate_text(GOOD) is None
    llm, _ = make(lambda **kw: reply(loop))
    m, ev = run(llm)
    assert m.content.startswith("Резерв:") and ("повтор" in ev[0]["reason"] or "строка" in ev[0]["reason"])


def test_same_answer_to_a_different_question_switches():
    llm, _ = make(lambda **kw: reply(GOOD + " Проверьте также тепловые зазоры клапанов по таблице."))
    m1, ev1 = run(llm, "Какой момент коренных?")
    assert ev1 == []
    m2, ev2 = run(llm, "Какой зазор клапанов?")  # next call of the same step, other question, same text -> loop
    assert m2.content.startswith("Резерв:") and "сходство" in ev2[0]["reason"]
    m3, ev3 = run(llm, "Какой зазор клапанов?", conv="other")  # another conversation: no comparison
    assert ev3 == []


def test_same_question_repeated_is_not_a_loop():
    llm, _ = make(lambda **kw: reply(GOOD + " Проверьте также тепловые зазоры клапанов по таблице."))
    run(llm, "Какой момент коренных?")
    _, ev = run(llm, "Какой момент коренных?")
    assert ev == []


def test_missing_cloudru_key_skips_reserve_with_journal_note(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    llm, seen = make(_raise(Http(503)), key="")
    with pytest.raises(LlmUnavailable):
        run(llm)
    _, ev = None, None
    ev = []
    t = REQUEST_LLM_EVENTS.set(ev)
    try:
        with pytest.raises(LlmUnavailable):
            llm.chat("agent_r0", [{"role": "user", "content": "x"}])
    finally:
        REQUEST_LLM_EVENTS.reset(t)
    assert seen["reserve"] == [] and ev and ev[0]["skipped"] and "CLOUDRU_API_KEY" in ev[0]["note"]
    llm2, _ = make(lambda **kw: reply(""), key="")
    m, ev2 = run(llm2)
    assert ev2 and ev2[0]["skipped"]  # degenerate but no reserve: answer kept, note in the journal


def test_tool_call_loop_goes_to_reserve_and_is_journaled(make_agent):
    same = lambda i: msg(None, [tool_call("search_manual", {"query": "момент затяжки коренных"}, i)])  # noqa: E731
    a = make_agent([same(1), same(2), same(3), msg("421-461 Н·м [S1].")])
    c = a.conv("u1", "M333YK47")
    turn = a.handle(c, {"lang": "ru"}, "Какой момент затяжки коренных? (просто текст)", event="alternatives")
    sw = [t for t in turn.tools if t["tool"] == "llm_fallback"]
    assert sw and "цикл вызовов" in sw[0]["reason"]
    assert sum(1 for t in turn.tools if t["tool"] == "search_manual" and not t.get("auto")) <= 2  # third identical call not run


def test_journal_shows_which_model_answered():
    from digital_mechanic.web.app import agent_journal
    j = agent_journal([{"tool": "llm_fallback", "from": "tokenrouter", "to": "cloudru", "model": "GigaChat/GigaChat-4-708B-A41", "reason": "HTTP 429"},
                       {"tool": "llm_answered", "provider": "cloudru", "model": "GigaChat/GigaChat-4-708B-A41", "step": "agent_r0"}])
    assert "HTTP 429" in j["steps"][0] and "GigaChat-4" in j["steps"][0] and "GigaChat" in j["steps"][1]
