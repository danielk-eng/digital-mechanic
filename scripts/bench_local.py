"""Benchmark the local GPU mode (docs/LOCAL_GPU.md) once the hardware is there.

  llm     TTFT, decode tok/s, aggregate tok/s at N parallel requests against any OpenAI-compatible server
          python scripts/bench_local.py llm --base http://localhost:8080/v1 --model local-llm --prompt-tokens 10000 --concurrency 1,3,8
  app     our citation eval (same metric as audit/METRICS.md: cite_acc, cite_prec, refuse_acc) through the running app
          python scripts/bench_local.py app --app http://localhost:8000 --qset eval/qset.json --label qwen38-27b
  compare side by side of saved runs per label (cite eval + LLM speed) against the current cloud baseline
          python scripts/bench_local.py compare --labels qwen38-27b,qwen3-30b-a3b
  speech  /stt latency (and WER if a .txt reference sits next to each audio file) against the speech service
          python scripts/bench_local.py speech --speech http://localhost:9000 --audio-dir audit/asr --lang ru

Results: runs/bench_local_<mode>_<time>.json (runs/ is git-ignored). Key from LOCAL_LLM_KEY / LLM_API_KEY env, never printed.
"""
import argparse, concurrent.futures as cf, json, os, re, statistics, sys, time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FILLER = ("Проверить зазоры клапанов на холодном двигателе. Момент затяжки болтов головки цилиндров указан в таблице. "
          "Давление масла на прогретом двигателе при номинальной частоте вращения должно быть в пределах нормы. ")


def _out(mode: str, data: dict, label: str = "") -> None:
    d = ROOT / "runs"; d.mkdir(exist_ok=True)
    tag = f"_{re.sub(r'[^A-Za-z0-9._-]', '_', label)}" if label else ""
    p = d / f"bench_local_{mode}{tag}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    data["label"] = label
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {p}")


def _context(n_tokens: int) -> str:
    """Russian manual-like text of about n_tokens (~3.2 chars per token for Qwen/GPT tokenizers on Russian; usage shows the real count)."""
    return (FILLER * (n_tokens * 32 // 10 // len(FILLER) + 1))[: n_tokens * 32 // 10]


def one_llm(base: str, model: str, key: str, prompt_tokens: int, max_tokens: int, seed: int, extra: dict | None = None) -> dict:
    msgs = [{"role": "system", "content": "Ты помощник механика. Отвечай по фрагментам руководства, кратко, по-русски."},
            {"role": "user", "content": f"[{seed}] ФРАГМЕНТЫ:\n{_context(prompt_tokens)}\n\nВопрос: перечисли шаги проверки и нормы подробно."}]
    body = {"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0.1, "stream": True,
            "stream_options": {"include_usage": True}, **(extra or {})}
    t0 = time.time(); ttft = None; n_chunks = 0; usage = {}
    with httpx.stream("POST", f"{base.rstrip('/')}/chat/completions", json=body, timeout=600,
                      headers={"Authorization": f"Bearer {key}"}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            ch = json.loads(line[5:])
            if ch.get("usage"):
                usage = ch["usage"]
            for c in ch.get("choices") or []:
                d = c.get("delta") or {}
                if d.get("content") or d.get("reasoning_content") or d.get("reasoning"):
                    n_chunks += 1
                    if ttft is None:
                        ttft = time.time() - t0
    total = time.time() - t0
    out = usage.get("completion_tokens") or n_chunks
    dec = out / (total - ttft) if ttft and total > ttft else None
    return {"ttft_s": round(ttft or total, 3), "total_s": round(total, 2), "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": out, "decode_tok_s": round(dec, 1) if dec else None}


def cmd_llm(a) -> None:
    key = a.api_key or os.getenv("LOCAL_LLM_KEY") or os.getenv("LLM_API_KEY") or "local"
    extra = json.loads(a.extra_body) if a.extra_body else None
    one_llm(a.base, a.model, key, 64, 8, -1, extra)  # warm-up
    res = {"base": a.base, "model": a.model, "prompt_tokens": a.prompt_tokens, "max_tokens": a.max_tokens, "extra_body": extra, "runs": []}
    for n in [int(x) for x in a.concurrency.split(",")]:
        t0 = time.time()
        with cf.ThreadPoolExecutor(n) as ex:
            rs = list(ex.map(lambda i: one_llm(a.base, a.model, key, a.prompt_tokens, a.max_tokens, i, extra), range(n * a.rounds)))
        wall = time.time() - t0
        row = {"concurrency": n, "requests": len(rs), "ttft_median_s": statistics.median(r["ttft_s"] for r in rs),
               "ttft_max_s": max(r["ttft_s"] for r in rs),
               "decode_per_user_tok_s": statistics.median(r["decode_tok_s"] for r in rs if r["decode_tok_s"]),
               "aggregate_out_tok_s": round(sum(r["completion_tokens"] for r in rs) / wall, 1),
               "turn_median_s": statistics.median(r["total_s"] for r in rs),
               "prompt_tokens_real": statistics.median(r["prompt_tokens"] or 0 for r in rs)}
        print(json.dumps(row, ensure_ascii=False)); res["runs"].append(row)
    _out("llm", res, a.label)


REFUSE = re.compile(r"нет данных|данных[^.]{0,40} нет|сведений[^.]{0,40} нет|данных нет|не указан|не приведен|нет сведений|не найден|"
                    r"не нашёл|не нашел|отсутству|нет информации|не содерж|в руководстве нет|нет в руководстве|не описан|не могу|"
                    r"не относится|вне|не по теме|только по|ничего нет|не знаю|не моя тема", re.I)


def _pages(data_root: Path) -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    import gzip
    from digital_mechanic.ingest.pages import load_pages
    by = {(p.document, p.page): re.sub(r"\s+", " ", p.text) for p in
          load_pages(str(data_root / "data/index/pages.jsonl.gz"), str(data_root / "data/engine_docs.yaml"))}
    extra = data_root / "data/index/extra_pages.jsonl.gz"
    if extra.exists():
        for l in gzip.open(extra, "rt", encoding="utf-8"):
            o = json.loads(l); by[(o["document"], o.get("page"))] = re.sub(r"\s+", " ", o.get("text", ""))
    return by


def cmd_app(a) -> None:
    by = _pages(Path(a.data_root))  # needs the OEM page index on disk (stand only, not in git)
    qs = [q for q in json.load(open(a.qset, encoding="utf-8"))["questions"] if q["kind"] in ("answer", "nodata")]
    qs = qs[: a.limit] if a.limit else qs

    def run(q):
        c = httpx.Client(base_url=a.app, timeout=240, follow_redirects=True)
        c.post("/api/login/demo", json={"account": a.account, "pin": os.getenv("DEMO_PIN", "3002"), "consent": True}).raise_for_status()
        c.post("/api/diagnose", json={"vehicle_id": q["vehicle"], "session_id": q["session"]})
        t = time.time(); r = c.post("/api/chat", json={"vehicle_id": q["vehicle"], "text": q["q"]}); dt = time.time() - t
        d = r.json() if r.status_code == 200 else {}
        text = d.get("text") or ""; src = [(s.get("document"), s.get("page")) for s in d.get("sources") or []]
        if q["kind"] == "answer":
            rx = re.compile(q["must"], re.I); hits = [s for s in src if rx.search(by.get(tuple(s), ""))]
            ok, prec = bool(hits), (len(hits) / len(src) if src else 0.0)
        else:
            ok, prec = bool(REFUSE.search(text[:250])), None
        return {"id": q["id"], "kind": q["kind"], "status": r.status_code, "sec": round(dt, 1), "ok": ok, "prec": prec, "sources": src}

    with cf.ThreadPoolExecutor(a.parallel) as ex:
        res = list(ex.map(run, qs))
    ans = [r for r in res if r["kind"] == "answer"]; nod = [r for r in res if r["kind"] == "nodata"]
    lat = sorted(r["sec"] for r in res)
    summ = {"app": a.app, "n": len(res), "cite_acc": round(sum(r["ok"] for r in ans) / len(ans), 3),
            "cite_prec": round(statistics.mean(r["prec"] for r in ans), 2),
            "refuse_acc": round(sum(r["ok"] for r in nod) / len(nod), 3) if nod else None,
            "lat_median": statistics.median(lat), "lat_p90": lat[int(0.9 * (len(lat) - 1) + 0.5)],
            "errors": sum(r["status"] != 200 for r in res), "baseline": {"cite_acc": 0.944, "cite_prec": 0.89, "refuse_acc": 0.75, "lat_median": 11.4}}
    print(json.dumps(summ, ensure_ascii=False))
    _out("app", {"summary": summ, "results": res}, a.label)


def _wer(ref: str, hyp: str) -> float:
    n = lambda s: re.sub(r"[^\w\s]", " ", s.lower()).split()
    r, h = n(ref), n(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


def cmd_speech(a) -> None:
    files = sorted(p for p in Path(a.audio_dir).iterdir() if p.suffix.lower() in (".wav", ".mp3", ".ogg", ".webm", ".m4a"))
    rows = []
    for p in files:
        lang = a.lang or p.stem.split("_")[0]
        t = time.time()
        r = httpx.post(f"{a.speech.rstrip('/')}/stt", files={"audio": (p.name, p.read_bytes())}, data={"lang": lang}, timeout=120)
        dt = time.time() - t
        j = r.json() if r.status_code == 200 else {}
        ref = p.with_suffix(".txt")
        row = {"file": p.name, "lang": lang, "sec": round(dt, 2), "model": j.get("model"), "text": j.get("text"),
               "wer": round(_wer(ref.read_text(encoding="utf-8"), j.get("text") or ""), 3) if ref.exists() else None}
        print(json.dumps(row, ensure_ascii=False)); rows.append(row)
    _out("speech", {"speech": a.speech, "rows": rows})


def _latest(mode: str, label: str) -> dict | None:
    fs = sorted((ROOT / "runs").glob(f"bench_local_{mode}_{label}_*.json"))
    return json.loads(fs[-1].read_text(encoding="utf-8")) if fs else None


def cmd_compare(a) -> None:
    """One row per label: cite eval from the latest `app` run, speed from the latest `llm` run (concurrency 1)."""
    base = {"label": "cloud (deepseek-v4.1-flash)", "cite_acc": 0.944, "cite_prec": 0.89, "refuse_acc": 0.75, "lat_median": 11.4}
    rows = [base]
    for lab in [x.strip() for x in a.labels.split(",") if x.strip()]:
        r = {"label": lab}
        app = _latest("app", lab)
        if app:
            r.update({k: app["summary"].get(k) for k in ("cite_acc", "cite_prec", "refuse_acc", "lat_median", "lat_p90", "errors")})
        llm = _latest("llm", lab)
        if llm and llm.get("runs"):
            one = min(llm["runs"], key=lambda x: x["concurrency"]); top = max(llm["runs"], key=lambda x: x["concurrency"])
            r.update({"ttft_s": one["ttft_median_s"], "decode_tok_s": one["decode_per_user_tok_s"],
                      f"aggregate_tok_s@{top['concurrency']}": top["aggregate_out_tok_s"]})
        rows.append(r)
    cols = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    print(" | ".join(cols)); print(" | ".join("---" for _ in cols))
    for r in rows:
        print(" | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols))
    _out("compare", {"rows": rows}, "_".join(x["label"] for x in rows[1:]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="mode", required=True)
    l = sp.add_parser("llm"); l.add_argument("--base", default="http://localhost:8080/v1"); l.add_argument("--model", default="local-llm")
    l.add_argument("--api-key", default=""); l.add_argument("--prompt-tokens", type=int, default=10000)
    l.add_argument("--max-tokens", type=int, default=900); l.add_argument("--concurrency", default="1,3,8"); l.add_argument("--rounds", type=int, default=2)
    l.add_argument("--label", default=""); l.add_argument("--extra-body", default="", help='e.g. {"chat_template_kwargs": {"enable_thinking": false}}')
    p = sp.add_parser("app"); p.add_argument("--app", default="http://localhost:8000"); p.add_argument("--qset", default=str(ROOT / "eval/qset.json"))
    p.add_argument("--account", default="selftaught"); p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--limit", type=int, default=0); p.add_argument("--label", default=""); p.add_argument("--data-root", default=str(ROOT), help="checkout with data/index (stand only)")
    s = sp.add_parser("speech"); s.add_argument("--speech", default="http://localhost:9000"); s.add_argument("--audio-dir", required=True)
    s.add_argument("--lang", default="")
    c = sp.add_parser("compare"); c.add_argument("--labels", required=True)
    a = ap.parse_args()
    {"llm": cmd_llm, "app": cmd_app, "speech": cmd_speech, "compare": cmd_compare}[a.mode](a)


if __name__ == "__main__":
    main()
