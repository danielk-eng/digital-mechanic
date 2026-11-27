"""Fill the translation base (export of scripts/export_for_translation.py) with machine drafts, glossary-aware.

    PYTHONPATH=src python scripts/translate_export.py PEREVOD_DIR [--groups dictionaries,histories,app_strings,ui_strings] [--langs uz,tg]

Only empty text_<lang> fields are filled; people's translations are never overwritten. Every draft goes through the same checks
as the import (scripts/import_translations.py: numbers, codes, [S1] labels, placeholders, script, length); a failed draft stays
empty and is listed in PEREVOD_DIR/translate_report.md. Glossary terms (glossary_ru.csv) are passed to the model with each batch.
Manual pages and section headings (OEM text) are not filled by default: their translations stay on the stand, never in git.
Then: scripts/import_translations.py PEREVOD_DIR --app .
"""
import argparse, csv, glob, importlib.util, json, re, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from digital_mechanic.agent.llm import Llm
from digital_mechanic.config import get_settings
from digital_mechanic.i18n import LANGS

_spec = importlib.util.spec_from_file_location("imp", Path(__file__).with_name("import_translations.py"))
imp = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(imp)

SYS = ("Ты переводишь тексты приложения для механиков автопарка (двигатели ЯМЗ, ЗМЗ, ремонт ДВС) с русского на {name} ({llm}). "
       "Правила: числа, единицы, коды (F01, S-0003, TKT-…), метки [S1]/[X1], марки и модели (ЯМЗ-236М2, МАЗ), плейсхолдеры "
       "({{n}}, %s, ${{x}}), HTML и эмодзи — без изменений. Регулярные выражения (с | ( ) \\b) переводи только по словам, "
       "служебные символы оставь. Термины — строго по глоссарию.\n{gloss}"
       "Верни JSON: {{\"items\": [\"перевод 1\", ...]}} — столько же элементов и в том же порядке, без пояснений.")


def glossary(path: Path) -> dict:
    g = defaultdict(dict)
    if path.is_file():
        for row in csv.DictReader(open(path, encoding="utf-8")):
            for l in LANGS:
                if row.get(l):
                    g[l][row["ru"].lower()] = row[l]
    return g


def hint(g: dict, lang: str, texts: list[str]) -> str:
    low = " ".join(texts).lower()
    hits = [(k, v) for k, v in g.get(lang, {}).items() if k in low][:40]
    return ("Глоссарий: " + "; ".join(f"{k} = {v}" for k, v in hits) + "\n") if hits else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("perevod")
    ap.add_argument("--groups", default="dictionaries,histories,app_strings,ui_strings")
    ap.add_argument("--langs", default=",".join(l for l in LANGS if l != "ru"))
    ap.add_argument("--batch", type=int, default=25); ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    root = Path(a.perevod); g = glossary(root / "glossary_ru.csv"); llm = Llm(get_settings())
    files = [f for grp in a.groups.split(",") for f in sorted(glob.glob(str(root / "data" / f"{grp}_*.jsonl")))]
    recs = {f: [json.loads(x) for x in open(f, encoding="utf-8")] for f in files}
    jobs = []
    for lang in a.langs.split(","):
        todo = [(f, i) for f in files for i, r in enumerate(recs[f]) if not (r.get(f"text_{lang}") or "").strip()]
        jobs += [(lang, todo[i:i + a.batch]) for i in range(0, len(todo), a.batch)]
    bad = defaultdict(list); done = defaultdict(int)

    def run(job):
        lang, chunk = job
        texts = [recs[f][i]["text_ru"] for f, i in chunk]
        sys_ = SYS.format(name=LANGS[lang]["name"], llm=LANGS[lang]["llm"], gloss=hint(g, lang, texts))
        for _ in range(2):
            try:
                items = llm.json("translate_export", sys_, json.dumps(texts, ensure_ascii=False), max_tokens=8000).get("items")
                if isinstance(items, list) and len(items) == len(texts):
                    break
            except Exception as e:  # noqa: BLE001
                print(lang, "retry", type(e).__name__, file=sys.stderr)
        else:
            bad[lang] += [(recs[f][i]["id"], "модель не вернула список") for f, i in chunk]; return
        for (f, i), out in zip(chunk, items):
            out = out if isinstance(out, str) else ""
            why = imp.check(recs[f][i], lang, out)
            if why:
                bad[lang].append((recs[f][i]["id"], why))
            else:
                recs[f][i][f"text_{lang}"] = out; done[lang] += 1

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(run, jobs))
    for f in files:
        with open(f, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in recs[f])
    rep = ["# Машинный черновик перевода", "", "| Язык | Заполнено | Не прошло проверку |", "|---|---|---|"]
    rep += [f"| {l} | {done[l]} | {len(bad[l])} |" for l in a.langs.split(",")]
    for l, xs in bad.items():
        rep += ["", f"## {l}", ""] + [f"- `{i}`: {w}" for i, w in xs[:300]]
    (root / "translate_report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep[:4 + len(a.langs.split(","))]))


if __name__ == "__main__":
    main()
