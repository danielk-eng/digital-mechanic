"""Export every Russian text of the app for human translation.

Usage: PYTHONPATH=src python scripts/export_for_translation.py OUT_DIR [--tickets deb_tickets.json]

Each record is one JSON line:
  {"id", "type", "source", "doc_id"?, "page"?, "text_ru", "text_uz": "", "text_tg": "", ..., "text_be": ""}
`id` is stable: import_translations.py maps the translation back by id (manual pages by doc_id + page,
so search in the user's language cites the original Russian page). Files are split at ~200 KB."""
from __future__ import annotations

import argparse
import ast
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
LANGS = ["uz", "tg", "kk", "hi", "vi", "bn", "ky", "be"]
CYR = re.compile(r"[А-Яа-яЁё]")
WORD = re.compile(r"[а-яё]{3,}")
EXCLUDE_DOCS = re.compile(r"KAMAZ|КАМАЗ", re.I)  # avtoalfa mirror stays out
PART = 200_000


def ru(s) -> bool:
    return isinstance(s, str) and bool(WORD.search(s)) and len(CYR.findall(s)) >= 2


def rec(id_, type_, source, text, **kw) -> dict:
    r = {"id": id_, "type": type_, "source": source, **kw, "text_ru": text}
    r.update({f"text_{l}": "" for l in LANGS})
    return r


def manual_pages():
    for fn, kind in (("data/index/pages.jsonl.gz", "engine"), ("data/index/extra_pages.jsonl.gz", "extra")):
        for line in gzip.open(ROOT / fn, "rt", encoding="utf-8"):
            p = json.loads(line)
            if EXCLUDE_DOCS.search(p["doc_id"]) or not ru(p.get("text") or ""):
                continue
            yield rec(f"page:{p['doc_id']}:{p['page']}", "manual_page", kind, p["text"], doc_id=p["doc_id"], page=p["page"],
                      document=p.get("document") or p.get("title", ""))


def headings():
    from digital_mechanic.ingest.pages import load_pages
    from digital_mechanic.retrieval.bm25 import load_extra
    from digital_mechanic.retrieval.sections import SectionIndex
    pages = load_pages(ROOT / "data/index/pages.jsonl.gz", ROOT / "data/engine_docs.yaml")
    pages += [p for p in load_extra(str(ROOT / "data/index/extra_pages.jsonl.gz")) if not EXCLUDE_DOCS.search(p.doc_id)]
    seen = set()
    for doc, hs in SectionIndex(pages).heads.items():
        for h in hs:
            if ru(h.title) and (doc, h.title) not in seen:
                seen.add((doc, h.title))
                yield rec(f"head:{doc}:{h.page}:{h.line}", "section_heading", "manual", h.title, doc_id=doc, page=h.page, num=h.num)


def dictionaries():
    for f in json.load(open(ROOT / "data/official/fault_types.json", encoding="utf-8")):
        yield rec(f"fault:{f['code']}", "fault_code", "fault_types.json", f["title"], code=f["code"])
    seen = set()
    v = json.load(open(ROOT / "data/official/vehicles.json", encoding="utf-8"))
    for veh in v["vehicles"]:
        for k in ("vehicle_type", "fuel"):
            if ru(veh.get(k)) and veh[k] not in seen:
                seen.add(veh[k]); yield rec(f"dict:{k}:{veh[k]}", "dictionary", "vehicles.json", veh[k])
        for s in veh.get("sessions", []):
            for k in ("description", "anomaly_title"):
                if ru(s.get(k)) and s[k] not in seen:
                    seen.add(s[k]); yield rec(f"dict:session:{s[k][:60]}", "diagnosis_text", "vehicles.json", s[k])


def tickets(path: Path | None):
    if path and path.exists():
        t = json.load(open(path, encoding="utf-8"))
        for tk in t["tickets"]:
            p, tid = tk["payload"], tk["ticket_id"]
            yield rec(f"ticket:{tid}:conclusion", "ticket", "deb", p["conclusion"], vehicle_id=tk["vehicle_id"])
            for i, op in enumerate(p.get("operations") or []):
                yield rec(f"ticket:{tid}:op:{i}", "ticket_operation", "deb", op, vehicle_id=tk["vehicle_id"])
            for i, sp in enumerate(p.get("spare_parts") or []):
                txt = sp if isinstance(sp, str) else sp.get("name", "")
                if ru(txt):
                    yield rec(f"ticket:{tid}:part:{i}", "spare_part", "deb", txt)
        for r in t["repairs"]:
            yield rec(f"repair:{r['id']}:title", "repair_title", "deb", r["title"], vehicle_id=r["vehicle_id"])
            for i, s in enumerate(r["steps"]):
                yield rec(f"repair:{r['id']}:step:{i}", "repair_step", "deb", s["text"], doc_id=s.get("doc_id"), page=s.get("page"))
                if ru(s.get("quote")):
                    yield rec(f"repair:{r['id']}:quote:{i}", "manual_quote", "deb", s["quote"], doc_id=s.get("doc_id"), page=s.get("page"))


def histories():
    h = json.load(open(ROOT / "data/demo_history.json", encoding="utf-8"))
    for e in h["events"]:
        tid = e["ticket_id"]
        for i, s in enumerate(e.get("repair_steps") or []):
            yield rec(f"hist:{tid}:step:{i}", "repair_step", "demo_history", s, vehicle_id=e["vehicle_id"])
        for i, s in enumerate(e.get("parts") or []):
            yield rec(f"hist:{tid}:part:{i}", "spare_part", "demo_history", s, vehicle_id=e["vehicle_id"])
        for k in ("note", "title", "summary"):
            if ru(e.get(k)):
                yield rec(f"hist:{tid}:{k}", "history", "demo_history", e[k], vehicle_id=e["vehicle_id"])


def app_strings():
    """Russian literals shown to people: Python (templates, chips, statuses) and JS outside i18n.json."""
    seen = set()
    for f in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and ru(n.value) and id(n) not in docs:
                s = n.value.strip()
                typ = "llm_prompt" if len(s) > 400 or "JSON" in s or "Верни" in s else "app_string"
                if typ == "llm_prompt" or s in seen:
                    continue  # prompts to the model are not shown to people
                seen.add(s)
                yield rec(f"py:{f.relative_to(ROOT / 'src')}:{n.lineno}", typ, str(f.relative_to(ROOT)), s)
    for f in sorted((ROOT / "src/digital_mechanic/web/static").glob("*.js")):
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            for m in re.finditer(r'"([^"\\]*[а-яё]{3,}[^"\\]*)"|`([^`]*[а-яё]{3,}[^`]*)`', line):
                s = (m.group(1) or m.group(2)).strip()
                if s in seen or s.startswith("//"):
                    continue
                seen.add(s)
                yield rec(f"js:{f.name}:{i}", "app_string", f"web/static/{f.name}", s)


def ui_missing():
    d = json.load(open(ROOT / "src/digital_mechanic/web/static/i18n.json", encoding="utf-8"))
    for k, v in d["ru"].items():
        miss = [l for l in LANGS if not d.get(l, {}).get(k) or (d[l][k] == v and ru(v))]
        if miss and ru(v):
            r = rec(f"ui:{k}", "ui_string", "i18n.json", v, missing=miss)
            for l in LANGS:
                if l not in miss:
                    r[f"text_{l}"] = d[l][k]  # already translated by the model: review, edit if needed
            yield r


def write(out: Path, name: str, rows: list[dict]) -> list[tuple[str, int, int]]:
    files, buf, size, n = [], [], 0, 1
    def flush():
        nonlocal buf, size, n
        if not buf:
            return
        fn = f"{name}_{n:03d}.jsonl"
        (out / fn).write_text("".join(buf), encoding="utf-8")
        files.append((fn, len(buf), (out / fn).stat().st_size))
        buf, size, n = [], 0, n + 1
    for r in rows:
        line = json.dumps(r, ensure_ascii=False) + "\n"
        if size + len(line.encode()) > PART and buf:
            flush()
        buf.append(line); size += len(line.encode())
    flush()
    return files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--tickets", default=None)
    a = ap.parse_args()
    out = Path(a.out); (out / "data").mkdir(parents=True, exist_ok=True)
    groups = [
        ("manual_pages", "Страницы руководств (двигатели + доп. руководства шасси, без КАМАЗ)", list(manual_pages())),
        ("section_headings", "Заголовки разделов руководств", list(headings())),
        ("dictionaries", "Коды неисправностей F01–F14, типы ТС, топливо, тексты диагностики", list(dictionaries())),
        ("tickets", "Заявки на дефектовку и ремонты со стенда deb (без ID пользователей)", list(tickets(Path(a.tickets) if a.tickets else None))),
        ("histories", "История ТС (демо): шаги ремонтов, запчасти", list(histories())),
        ("app_strings", "Строки приложения: шаблоны, чипы, статусы (Python и JS)", list(app_strings())),
        ("ui_strings", "Строки интерфейса i18n.json без перевода (или переведённые моделью, на проверку)", list(ui_missing())),
    ]
    index = []
    for name, title, rows in groups:
        index.append((name, title, rows, write(out / "data", name, rows)))
    tot_rec = sum(len(r) for _, _, r, _ in index)
    tot_sz = sum(sz for *_, fs in index for _, _, sz in fs)
    lines = ["# Экспорт русского контента для перевода", "",
             f"Записей: {tot_rec}, файлов: {sum(len(fs) for *_, fs in index)}, объём: {tot_sz / 1e6:.1f} МБ (файлы до ~200 КБ).",
             f"Языки (поля для заполнения): {', '.join('text_' + l for l in LANGS)}.", "",
             "Формат: JSONL, одна запись на строку. Менять можно только поля text_<язык>; id, type, doc_id, page и text_ru не трогать.",
             "Как переводить: ПРОМТЫ.md; термины: glossary_ru.csv; обратно в приложение: import_translations.py (см. ИМПОРТ.md).", "",
             "| Группа | Что внутри | Записей | Символов (ru) | Файлы |", "|---|---|---|---|---|"]
    for name, title, rows, fs in index:
        chars = sum(len(r["text_ru"]) for r in rows)
        lines.append(f"| {name} | {title} | {len(rows)} | {chars:,} | {len(fs)} |".replace(f"{chars:,}", f"{chars:,}".replace(",", " ")))
    lines += ["", "## Файлы", "", "| Файл | Записей | Размер, КБ |", "|---|---|---|"]
    for *_, fs in index:
        lines += [f"| data/{fn} | {n} | {sz / 1024:.0f} |" for fn, n, sz in fs]
    pages = [r for r in index[0][2]]
    docs = {}
    for r in pages:
        docs.setdefault((r["doc_id"], r["document"]), 0)
        docs[(r["doc_id"], r["document"])] += 1
    lines += ["", "## Руководства в manual_pages", "", "| doc_id | Документ | Страниц |", "|---|---|---|"]
    lines += [f"| {d} | {t[:90]} | {n} |" for (d, t), n in sorted(docs.items())]
    lines += ["", "Руководства Урал (EXTRA-URAL*) по титулу запрещено перепечатывать без разрешения изготовителя: перевод только для",
              "внутреннего поиска и цитирования в приложении, не для публикации."]
    (out / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:20]))


if __name__ == "__main__":
    main()
