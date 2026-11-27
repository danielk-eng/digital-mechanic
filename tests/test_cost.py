"""Cost calculator (organizers' rule 1, day-2 (b)): one scenario = diagnosis -> answer/ticket, every model step,
indexing as a separate one-time line, fleets 35/50/370/800, free primary = 0 ₽ plus a paid equivalent."""
from fastapi.testclient import TestClient

from digital_mechanic.cost import calc

M = {"measured_at": "t", "model": "m", "runs": 3, "scenario": "s", "tokens_per_scenario_median": 30000,
     "lines": {"explain": {"calls_per_scenario": 1, "in_per_call": 8000, "out_per_call": 200},
               "answer": {"calls_per_scenario": 2, "in_per_call": 3000, "out_per_call": 200},
               "expand": {"calls_per_scenario": 1, "in_per_call": 1500, "out_per_call": 450},
               "extract": {"calls_per_scenario": 2, "in_per_call": 1600, "out_per_call": 220},
               "ticket": {"calls_per_scenario": 1, "in_per_call": 8000, "out_per_call": 300}}}


def test_scenario_sums_all_steps_and_free_is_zero():
    rows = calc.scenario(M, questions=2)
    assert [r["step"] for r in rows] == calc.ORDER and "embed" in calc.ORDER
    t = calc.totals(rows)
    assert t["in"] == 8000 + 2 * 3000 + 1500 + 2 * 1600 + 8000
    assert t["primary_free"] == 0 and t["fallback_free"] == 0
    exp = sum(r["calls"] * (r["in"] * 0.30 + r["out"] * 1.20) for r in rows) * calc.USD_RUB / 1e6
    assert abs(t["primary_paid"] - exp) < 1e-9 and t["fallback_paid"] > t["primary_paid"]


def test_fleets_indexing_complex_repair():
    r = calc.report(M)
    assert [f["vehicles"] for f in r["fleets"]] == [35, 50, 370, 800]
    f50 = r["fleets"][1]
    assert abs(f50["checks_month"] - 50 * 365 / 14 / 12) < 0.1 and f50["support"] > 0
    assert f50["total_primary_free"] == f50["server"] + f50["support"]
    assert r["indexing"]["bm25_rub"] == 0 and r["indexing"]["bge_m3_rub"] > 0
    assert r["complex_repair"]["calls"] > r["total"]["calls"]
    big = r["fleets"][3]
    assert big["models_primary_paid"] > f50["models_primary_paid"]


def test_calculator_public_without_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "c.db"))
    from digital_mechanic.web import app as m

    c = TestClient(m.app)
    j = c.get("/api/cost").json()
    assert j["total"]["primary_free"] == 0 and len(j["fleets"]) == 4
    h = c.get("/calculator")
    assert h.status_code == 200 and "calculator.xlsx" in h.text and "индексация" in h.text
    assert c.get("/static/calculator.xlsx").status_code == 200


def test_agent_journal_steps_and_errors():
    from digital_mechanic.web.app import agent_journal

    j = agent_journal([{"tool": "check_result", "ok": True}, {"tool": "search_manual", "args": {"query": "давление масла"}, "ok": True,
                        "hits": ["S1:ЯМЗ-236 РЭ стр.19", "S2:РК стр.267"]}, {"tool": "llm", "error": "timeout"},
                       {"tool": "uncited_guard", "removed": ["x"]}])
    assert j["steps"][1].startswith("2. искал в руководстве двигателя: «давление масла» → 2 фрагм.")
    assert "ОШИБКА" in j["steps"][2] and j["errors"] == ["llm: timeout"] and "удалено: 1" in j["steps"][3]


def test_cached_tokens_from_provider_usage():
    from types import SimpleNamespace as N
    from digital_mechanic.agent.llm import cached_tokens
    assert cached_tokens(None) == 0
    assert cached_tokens(N(prompt_tokens=100, prompt_tokens_details=N(cached_tokens=64))) == 64
    assert cached_tokens(N(prompt_tokens=100, prompt_tokens_details={"cached_tokens": 32})) == 32
    assert cached_tokens(N(prompt_tokens=100, prompt_cache_hit_tokens=128)) == 128
    assert cached_tokens(N(prompt_tokens=100, prompt_tokens_details=None)) == 0


def test_scx_cached_input_price_blended_per_step():
    from digital_mechanic.cost import calc
    p = calc.PRICES["primary_paid"]
    assert p["usd"] == (0.30, 1.20) and p["usd_cached"] == 0.03 and "scx.ai" in p["source"]
    assert abs(calc.in_price(p, 0.0) - 0.30 * calc.USD_RUB) < 1e-9
    assert abs(calc.in_price(p, 1.0) - 0.03 * calc.USD_RUB) < 1e-9
    m = {"lines": {"explain": {"calls_per_scenario": 1, "in_per_call": 1_000_000, "out_per_call": 0, "cached_share_provider": 0.5}}}
    row = next(r for r in calc.scenario(m, questions=0, ticket=False) if r["step"] == "explain")
    assert row["cached_share"] == 0.5
    assert abs(row["primary_paid"] - 0.165 * calc.USD_RUB) < 1e-6  # 0.5*0.03 + 0.5*0.30 = $0.165 per 1M
    assert abs(row["fallback_paid"] - 78.87) < 1e-6  # no cached rate -> full price
