"""Import people's translations back into the app.

Usage: PYTHONPATH=src python scripts/import_translations.py PEREVOD_DIR [--app .] [--langs uz,tg] [--force] [--dry-run]

Reads PEREVOD_DIR/data/*.jsonl (fields text_<lang> filled) and PEREVOD_DIR/glossary_ru.csv, validates every record and writes:
  data/i18n/pages_<lang>.jsonl.gz  manual pages (doc_id, page, text): multilingual search index; answers still cite the Russian page
  data/i18n/strings_<lang>.json    {russian text: translation} for app strings, headings, steps, tickets, codes
  data/i18n/glossary.json          {lang: {ru term: translation}} for the on-the-fly translator
  src/digital_mechanic/web/static/i18n.json  ui_string records merged into the language sections (keys "ui:<key>")
  PEREVOD_DIR/import_report.md     what was taken and what was rejected, with the reason
A record that fails a check is rejected (with --force it is taken and only reported).
"""
import argparse, csv, glob, gzip, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

LANGS = ["uz", "tg", "kk", "hi", "vi", "bn", "ky", "be"]
NUM = re.compile(r"\d+(?:[.,]\d+)?")
TOKENS = re.compile(r"\[(?:S|X)\d+\]|\b[A-Z]{1,4}-?\d[\w.-]*|\b[А-ЯЁ]{2,5}-\d[\w.-]*|\bF\d{2}\b|\bTKT-[\w-]+|\bS-\d+")
PLACEHOLDER = re.compile(r"\{[\w.]*\}|%\(?\w*\)?[sd]|\$\{[^}]*\}")
CYR, LAT = re.compile(r"[а-яёА-ЯЁ]"), re.compile(r"[a-zA-Z]")
SCRIPT = {  # letters that must be present, and Russian-only letters that must not remain in running text
    "uz": (re.compile(r"[a-zA-Zʻʼ‘’]"), CYR), "vi": (re.compile(r"[a-zA-ZÀ-ỹ]"), CYR),
    "tg": (re.compile(r"[а-яёғӣқӯҳҷ]", re.I), None), "kk": (re.compile(r"[а-яёәғқңөұүһі]", re.I), None),
    "ky": (re.compile(r"[а-яёңөү]", re.I), None), "be": (re.compile(r"[а-яёіў]", re.I), re.compile(r"[ищъ]", re.I)),
    "hi": (re.compile(r"[\u0900-\u097F]"), CYR), "bn": (re.compile(r"[\u0980-\u09FF]"), CYR),
}
PAGE_TYPES = {"manual_page"}
GLOSS_OK = {"проверено", "checked", "ok"}


def strip_codes(s: str) -> str:
    return TOKENS.sub(" ", s)


def check(rec: dict, lang: str, out: str) -> str | None:
    """None when the translation is safe to take, else the reason."""
    src = rec["text_ru"]
    if not out.strip():
        return "пусто"
    a = Counter(x.replace(",", ".") for x in NUM.findall(src))
    b = Counter(x.replace(",", ".") for x in NUM.findall(out))
    lost = [k for k, v in a.items() if b[k] < v]
    new = [k for k in b if k not in a]
    if lost:
        return "потеряны числа: " + ", ".join(lost[:6])
    if new:
        return "лишние числа: " + ", ".join(new[:6])
    miss = [t for t in TOKENS.findall(src) if t not in out]
    if miss:
        return "потеряны коды/номера/метки: " + ", ".join(miss[:6])
    ph = [p for p in PLACEHOLDER.findall(src) if p not in out]
    if ph:
        return "потеряны подстановки: " + ", ".join(ph)
    if src.lstrip().startswith(("{", "[")):
        try:
            sj, oj = json.loads(src), json.loads(out)
            if type(sj) is not type(oj) or (isinstance(sj, dict) and set(sj) != set(oj)):
                return "изменена структура JSON"
        except ValueError:
            return "сломан JSON"
    need, banned = SCRIPT[lang]
    body = strip_codes(out)
    if not need.search(body):
        return f"нет букв нужной письменности ({lang})"
    if banned is not None:
        words = [w for w in re.findall(r"\w+", body) if banned.search(w) and len(w) > 2]
        if len(words) > max(2, len(re.findall(r"\w+", body)) // 10):
            return "остались слова в чужой письменности: " + ", ".join(words[:5])
    if out.strip() == src.strip() and len(src) > 12:
        return "не переведено (совпадает с русским)"
    r = len(out) / max(1, len(src))
    if len(src) > 40 and not 0.3 <= r <= 3.5:
        return f"подозрительная длина ({r:.1f}× от оригинала)"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("perevod"); ap.add_argument("--app", default=".")
    ap.add_argument("--langs", default=",".join(LANGS)); ap.add_argument("--force", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src_dir, app = Path(a.perevod), Path(a.app)
    langs = [l for l in a.langs.split(",") if l in LANGS]
    pages, strings, ui = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    took, rej = Counter(), defaultdict(list)
    for f in sorted(glob.glob(str(src_dir / "data" / "*.jsonl"))):
        for n, line in enumerate(open(f, encoding="utf-8"), 1):
            try:
                rec = json.loads(line)
            except ValueError:
                rej["*"].append((Path(f).name, n, "", "строка не JSON")); continue
            for lang in langs:
                out = (rec.get(f"text_{lang}") or "").strip()
                if not out:
                    continue
                why = check(rec, lang, out)
                if why:
                    rej[lang].append((Path(f).name, n, rec["id"], why))
                    if not a.force:
                        continue
                took[lang] += 1
                if rec["type"] in PAGE_TYPES and rec.get("doc_id") and rec.get("page") is not None:
                    pages[lang][(rec["doc_id"], int(rec["page"]))] = out
                elif rec["type"] == "ui_string":
                    ui[lang][rec["id"].split(":", 1)[1]] = out
                else:
                    strings[lang][rec["text_ru"]] = out
    gloss = defaultdict(dict)
    gpath = src_dir / "glossary_ru.csv"
    if gpath.is_file():
        for row in csv.DictReader(open(gpath, encoding="utf-8")):
            for lang in langs:
                if row.get(lang):
                    gloss[lang][row["ru"]] = row[lang]
    out_dir = app / "data" / "i18n"
    rep = ["# Импорт переводов", "", "| Язык | Принято | Отклонено | Страниц | Строк | UI |", "|---|---|---|---|---|---|"]
    rep += [f"| {l} | {took[l]} | {len(rej[l])} | {len(pages[l])} | {len(strings[l])} | {len(ui[l])} |" for l in langs]
    for l in ["*"] + langs:
        if rej[l]:
            rep += ["", f"## Отклонено: {l}", ""] + [f"- {f}:{n} `{i}`: {w}" for f, n, i, w in rej[l][:500]]
    if not a.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        for l in langs:
            if pages[l]:
                with gzip.open(out_dir / f"pages_{l}.jsonl.gz", "wt", encoding="utf-8") as fh:
                    for (d, p), t in sorted(pages[l].items()):
                        fh.write(json.dumps({"doc_id": d, "page": p, "text": t}, ensure_ascii=False) + "\n")
            if strings[l]:
                old = out_dir / f"strings_{l}.json"
                merged = {**(json.loads(old.read_text(encoding="utf-8")) if old.is_file() else {}), **strings[l]}
                old.write_text(json.dumps(merged, ensure_ascii=False, indent=0), encoding="utf-8")
        if gloss:
            (out_dir / "glossary.json").write_text(json.dumps(gloss, ensure_ascii=False, indent=1), encoding="utf-8")
        if any(ui.values()):
            ip = app / "src/digital_mechanic/web/static/i18n.json"
            d = json.loads(ip.read_text(encoding="utf-8"))
            for l in langs:
                d.setdefault(l, {}).update(ui[l])
            ip.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # same layout as the file
    (src_dir / "import_report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep[:4 + len(langs)]))
    return 0 if a.force or not any(rej[l] for l in langs) else 1


if __name__ == "__main__":
    sys.exit(main())
