"""Glossary of workshop terms for translators: glossary_ru.csv (ru + one column per language).
Terms are picked from the most frequent words of the indexed manuals; translations are a DRAFT by the model
(column status = "черновик"), the translator corrects them and sets status = "проверено".
Usage: PYTHONPATH=src python scripts/make_glossary.py OUT.csv"""
import csv, gzip, json, re, sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from digital_mechanic.agent.llm import Llm
from digital_mechanic.config import get_settings

LANGS = ["uz", "tg", "kk", "ky"]
SCRIPT = {"uz": "узбекский, латиница", "tg": "таджикский, кириллица", "kk": "казахский, кириллица", "ky": "киргизский, кириллица"}
llm = Llm(get_settings())
cnt = Counter()
for fn in ("data/index/pages.jsonl.gz", "data/index/extra_pages.jsonl.gz"):
    for line in gzip.open(fn, "rt", encoding="utf-8"):
        t = json.loads(line)["text"].lower()
        words = re.findall(r"[а-яё]{4,}", t)
        cnt.update(words)
        cnt.update(f"{a} {b}" for a, b in zip(words, words[1:]))
cand = [w for w, n in cnt.most_common(2500) if n >= 8][:1400]
PICK = ("Ты терминолог по ремонту грузовиков и двигателей. Из списка частых слов и словосочетаний руководств выбери ровно 55 "
        "ключевых технических терминов (узлы, детали, операции, неисправности, измерения), которые механик должен понимать однозначно. "
        "Приведи каждый к словарной форме (именительный падеж). Категория: узел, деталь, операция, неисправность, измерение, материал, инструмент. "
        "Не повторяй термины из списка exclude. Верни JSON {\"terms\": [{\"ru\": \"...\", \"cat\": \"...\"}]}.")
terms = {}
for part in [cand[i:i + 350] for i in range(0, 1400, 350)]:
    for _ in range(2):
        try:
            r = llm.json("glossary", PICK, json.dumps({"words": part, "exclude": list(terms)}, ensure_ascii=False), max_tokens=3000)
            for t in r.get("terms", []):
                if t.get("ru"):
                    terms.setdefault(t["ru"].strip().lower(), t)
            break
        except Exception as e:  # noqa: BLE001
            print("retry", type(e).__name__)
terms = list(terms.values())[:220]
print("terms", len(terms))
SYS = ("Переведи технические термины мастерской на 4 языка. Нужен термин, которым реально пользуются механики (допустимы устоявшиеся "
       "заимствования из русского, если так говорят в мастерских, иначе литературный термин). Языки и письмо: "
       + "; ".join(f"{k} = {v}" for k, v in SCRIPT.items())
       + ". Верни JSON {\"items\": [{\"ru\": \"...\", " + ", ".join(f"\"{l}\": \"...\"" for l in LANGS) + "}]} в том же порядке.")
def run(ch):
    for _ in range(2):
        try:
            r = llm.json("glossary", SYS, json.dumps([t["ru"] for t in ch], ensure_ascii=False), max_tokens=5000)
            return {it["ru"].strip().lower(): it for it in r.get("items", []) if it.get("ru")}
        except Exception as e:  # noqa: BLE001
            print("retry", type(e).__name__)
    return {}
chunks = [terms[i:i + 15] for i in range(0, len(terms), 15)]
tr = {}
with ThreadPoolExecutor(4) as ex:
    for part in ex.map(run, chunks):
        tr.update(part)
with open(sys.argv[1], "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ru", "category", *LANGS, "status", "note"])
    for t in sorted(terms, key=lambda t: (t.get("cat", ""), t["ru"])):
        it = tr.get(t["ru"].strip().lower(), {})
        w.writerow([t["ru"], t.get("cat", ""), *[it.get(l, "") for l in LANGS], "черновик" if it else "", ""])
print("written", len(terms), "translated", sum(1 for t in terms if t["ru"].strip().lower() in tr))
