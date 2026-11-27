"""Cost calculator (organizers' answer in the task chat, 26.11, and the day-2 briefing, 27.11).

* One request = one user scenario: from the diagnosis result to the answer to the mechanic or the ticket. All model
  steps inside are summed: tool choice + answer (one agent call does both), retries, «Подробнее», citation extracts,
  ticket draft. Query embeddings are included too: retrieval is BM25 on the server, so they cost 0 tokens.
* Manual indexing is a separate one-time line.
* Per-step breakdown + recalculation for a fleet with explicit assumptions.
Token counts: cost/measured.json (scripts/cost_measure.py on the real model). Prices: without VAT, as the
template asks. Rate: CBR on 26.11.2026.
"""
from __future__ import annotations

import json
from pathlib import Path

USD_RUB = 82.3130  # CBR, 26.11.2026
SCX_URL = "https://scx.ai/models/deepseek-v4-1-flash-1017"  # paid price of the same model (V, 27.11)
PRICES = {  # per 1M tokens, RUB without VAT
    "primary_free": {"name": "deepseek-v4.1-flash (free, TokenRouter) — основная на стенде", "in": 0.0, "out": 0.0},
    "primary_paid": {"name": "DeepSeek-V4.1-flash-1017 (SCX.ai): $0,30 вход / $0,03 вход из кэша / $1,20 выход за 1 млн",
                     "in": 0.30 * USD_RUB, "in_cached": 0.03 * USD_RUB, "out": 1.20 * USD_RUB,
                     "usd": (0.30, 1.20), "usd_cached": 0.03, "source": SCX_URL},
    "fallback_free": {"name": "GigaChat-4-708B-A41 (Cloud.ru, без тарифа) — резерв и РФ-регион", "in": 0.0, "out": 0.0},
    "fallback_paid": {"name": "GigaChat Ultra на Cloud.ru (78,87 / 236,56 ₽ за 1 млн без НДС) — платный аналог резерва", "in": 78.87, "out": 236.56},
}
EMBED_BGE_M3_RUB = 0.50  # Cloud.ru bge-m3, per 1M input tokens without VAT (if BM25 were replaced by embeddings)
INDEX_TOKENS = 650_000  # 6 engine manuals, 1 743 pages, 1,82 млн знаков ≈ 0,65 млн токенов

STEP_RU = {
    "explain": "Объяснение вердикта (выбор инструмента + поиск BM25 + ответ)",
    "answer": "Вопрос механика (выбор инструмента + поиск BM25 + ответ)",
    "retry": "Повторные вызовы (исправление чисел без источника)",
    "expand": "«Подробнее» (развёрнутый ответ, готовится в фоне)",
    "extract": "Выжимки цитат (по нажатию на страницу)",
    "ticket": "Черновик заявки (выбор работ с цитатами)",
    "embed": "Эмбеддинги запроса (поиск BM25 на сервере — модели нет)",
}
ORDER = ["explain", "answer", "retry", "expand", "extract", "ticket", "embed"]

ASSUMPTIONS = {
    "check_every_days": 14, "questions_per_check": 2.5, "ticket_share": 0.35,
    "server_rub_month": 1787.0,  # Cloud.ru 2 vCPU 30 % / 4 ГБ / 30 ГБ NVMe + IP: 59,56 ₽/сутки
    "support_rate_rub_h": 2000.0,
}
SUPPORT_HOURS = {35: 6, 50: 8, 370: 24, 800: 40}  # обновление руководств, мониторинг, ответы на «пробелы в базе»
FLEETS = [35, 50, 370, 800]


def load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else Path(__file__).with_name("measured.json")
    return json.loads(p.read_text(encoding="utf-8"))


def cached_share(line: dict) -> float:
    """Share of a step's input tokens billed at the cached price: the provider-reported cached tokens when the provider
    reports caching, else the longest-common-prefix estimate (scripts/cost_measure.py)."""
    return float(line.get("cached_share_provider") or line.get("cached_share_prefix") or 0.0)


def in_price(p: dict, share: float) -> float:
    """Effective input price per 1M tokens: blended cached / uncached for prices that have a cached rate."""
    return share * p["in_cached"] + (1 - share) * p["in"] if "in_cached" in p else p["in"]


def scenario(measured: dict, questions: float = 2.5, ticket: bool = True) -> list[dict]:
    """Per-step lines of one scenario: calls, tokens in/out per call, cost under every price set."""
    L = measured["lines"]
    calls = {
        "explain": L.get("explain", {}).get("calls_per_scenario", 1.0),
        "answer": questions,
        "retry": L.get("retry", {}).get("calls_per_scenario", 0.0),
        "expand": L.get("expand", {}).get("calls_per_scenario", 1.0),
        "extract": L.get("extract", {}).get("calls_per_scenario", 2.0),
        "ticket": (L.get("ticket", {}).get("calls_per_scenario", 1.0) if ticket else 0.0),
        "embed": 0.0,
    }
    ans = L.get("answer", {})
    per_q = ans.get("calls_per_scenario", 2.0) / 2.0  # measured with 2 questions; agent rounds per question
    calls["answer"] = round(questions * per_q, 2)
    rows = []
    for k in ORDER:
        d = L.get(k, ans if k == "answer" else {})
        tin, tout = int(d.get("in_per_call", 0)), int(d.get("out_per_call", 0))
        share = cached_share(d)
        row = {"step": k, "name": STEP_RU[k], "calls": calls[k], "in": tin, "out": tout, "cached_share": share}
        for pk, p in PRICES.items():
            row[pk] = calls[k] * (tin * in_price(p, share) + tout * p["out"]) / 1e6
        rows.append(row)
    return rows


def totals(rows: list[dict]) -> dict:
    t = {"calls": sum(r["calls"] for r in rows), "in": sum(r["calls"] * r["in"] for r in rows), "out": sum(r["calls"] * r["out"] for r in rows)}
    for pk in PRICES:
        t[pk] = sum(r[pk] for r in rows)
    return t


def fleet(measured: dict, vehicles: int, a: dict | None = None) -> dict:
    a = {**ASSUMPTIONS, **(a or {})}
    checks = vehicles * 365 / a["check_every_days"] / 12
    with_t, no_t = totals(scenario(measured, a["questions_per_check"], True)), totals(scenario(measured, a["questions_per_check"], False))
    per_check = {pk: a["ticket_share"] * with_t[pk] + (1 - a["ticket_share"]) * no_t[pk] for pk in PRICES}
    support = SUPPORT_HOURS.get(vehicles, max(6, round(vehicles / 20))) * a["support_rate_rub_h"]
    server = a["server_rub_month"] * (1 if vehicles <= 370 else 2)
    out = {"vehicles": vehicles, "checks_month": round(checks, 1), "server": server, "support": support,
           "support_hours": SUPPORT_HOURS.get(vehicles, max(6, round(vehicles / 20)))}
    for pk in PRICES:
        out[f"models_{pk}"] = per_check[pk] * checks
        out[f"total_{pk}"] = per_check[pk] * checks + server + support
    return out


def complex_repair(measured: dict, exchanges: int = 50) -> dict:
    rows = scenario(measured, exchanges - 1, True)
    return totals(rows)


def indexing() -> dict:
    return {"tokens": INDEX_TOKENS, "bm25_rub": 0.0, "bge_m3_rub": INDEX_TOKENS * EMBED_BGE_M3_RUB / 1e6}


def report(measured: dict | None = None) -> dict:
    m = measured or load()
    rows = scenario(m)
    return {"measured": {**{k: m[k] for k in ("measured_at", "model", "runs", "scenario", "tokens_per_scenario_median")},
                         "cache": m.get("cache", {})},
            "prices": PRICES, "usd_rub": USD_RUB, "rows": rows, "total": totals(rows), "assumptions": ASSUMPTIONS,
            "fleets": [fleet(m, n) for n in FLEETS], "complex_repair": complex_repair(m), "indexing": indexing()}
