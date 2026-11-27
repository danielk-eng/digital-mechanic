"""Measure the tokens of one user scenario (organizers 26.11: from the diagnosis result to the answer / ticket,
ALL model steps summed) on the real model, and write src/digital_mechanic/cost/measured.json (no secrets, no manual text).

Scenario (typical procedure, day-2 briefing: 3-5 exchanges): diagnosis (code, 0 tokens) -> explanation of the
verdict -> 2 mechanic questions -> «Подробнее» (prefetched in the background) -> 2 citation extracts -> ticket draft ->
confirm (code, 0 tokens). Retrieval is BM25 on the server: no embedding model, 0 tokens (stated in the calculator).

    LLM_API_KEY_FILE=/workspace/.tokenrouter_key EMBED_MOCKS=1 python scripts/cost_measure.py [--runs 3]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STEP_OF = {  # Llm.chat step name -> calculator line
    "agent_r": "answer", "render_": "answer", "agent_fix_numbers": "retry",
    "expand": "expand", "section_extract": "extract", "draft": "ticket",
}
SCENARIOS = [
    ("E549YT198", "S-0011", ["Как проверить давление масла?", "Какое давление должно быть на прогретом двигателе?"]),
    ("B418HK98", "S-0009", ["Как отрегулировать тепловые зазоры клапанов?", "Какой зазор для впускных и выпускных?"]),
    ("M199CH98", "S-0190", ["Как проверить давление масла?", "Что делать, если давление ниже нормы?"]),
]


def line_of(step: str, phase: str) -> str:
    """Calculator line by the scenario phase; retries (number fixes) separately. A question is answered by the agent
    call(s) or by the two-pass path (facts + per-person rendering) -- both are «answer»."""
    if step.startswith("agent_fix_numbers"):
        return "retry"
    return {"explain": "explain", "question": "answer", "expand": "expand", "extract": "extract", "ticket": "ticket"}.get(phase, "other")


def run(runs: int) -> dict:
    os.environ.setdefault("EMBED_MOCKS", "1")
    os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "cost.db")
    from digital_mechanic.bootstrap import build_agent

    a = build_agent()
    a.expand_prefetch = False  # «Подробнее» is measured explicitly below (same prompt as the background run)
    per = defaultdict(lambda: {"calls": 0, "in": [], "out": [], "names": []})
    scen_tokens = []
    for i in range(runs):
        vid, sid, qs = SCENARIOS[i % len(SCENARIOS)]
        user = {"id": f"cost{i}", "lang": "ru", "role": "mechanic", "detail": "brief", "experience": "experienced"}
        c = a.conv(user["id"], vid)
        a.diagnose(c, sid)
        before = len(a.llm.usage.per_step)
        marks = []

        def phase(name, fn):
            n0 = len(a.llm.usage.per_step)
            r = fn()
            marks.append((name, n0, len(a.llm.usage.per_step)))
            return r

        t = phase("explain", lambda: a.handle(c, user, "", event="explain_diagnosis"))
        for q in qs:
            t = phase("question", lambda q=q: a.handle(c, user, q))
        phase("expand", lambda: a.handle(c, user, t.text, event="expand", sources=t.sources))
        for s in (t.sources or [])[:2]:
            phase("extract", lambda s=s: a.section_extract(s["doc_id"], s["page"], "", qs[-1]))
        phase("ticket", lambda: a.handle(c, user, "Создать заявку на дефектовку"))
        total = 0
        for name, n0, n1 in marks:
            for st in a.llm.usage.per_step[n0:n1]:
                ln = line_of(st["step"], name)
                per[ln]["calls"] += 1; per[ln]["names"].append(st["step"])
                per[ln]["in"].append(st["prompt_tokens"]); per[ln]["out"].append(st["completion_tokens"])
                total += st["prompt_tokens"] + st["completion_tokens"]
        scen_tokens.append(total)
        print(f"run {i + 1}: {vid} {sid}: {len(a.llm.usage.per_step) - before} model calls, {total} tokens", flush=True)
    out = {"measured_at": time.strftime("%Y-%m-%d %H:%M"), "model": a.llm.s.llm_model, "runs": runs,
           "scenario": "диагностика → объяснение → 2 вопроса → «Подробнее» → 2 выжимки цитат → черновик заявки → подтверждение",
           "tokens_per_scenario_median": int(statistics.median(scen_tokens)) if scen_tokens else 0, "lines": {}}
    for ln, d in per.items():
        out["lines"][ln] = {"calls_per_scenario": round(d["calls"] / runs, 2), "steps": sorted(set(d.get("names", []))),
                            "in_per_call": int(statistics.mean(d["in"])) if d["in"] else 0,
                            "out_per_call": int(statistics.mean(d["out"])) if d["out"] else 0}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "src/digital_mechanic/cost/measured.json"))
    r = run(ap.parse_args().runs)
    Path(ap.parse_args().out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=1))
