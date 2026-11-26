"""Grounded extraction: every work, part and citation must be backed by a verbatim quote
from a retrieved page of the vehicle's own engine manual. Nothing else reaches a ticket."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..retrieval.bm25 import Hit
from ..schemas import PartItem, SourcePage, WorkItem

MAX_PAGE_CHARS = 2600


def norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[-‐‑–—]\s*\n\s*", "", s)  # hyphenated line breaks
    s = re.sub(r"[\s\u00a0]+", " ", s)
    s = re.sub(r"[«»\"'`“”„.,;:()\[\]]", "", s)
    return s.strip()


@dataclass
class Fragment:
    label: str
    hit: Hit

    @property
    def page(self):
        return self.hit.page

    def prompt_text(self) -> str:
        return f"[{self.label}] {self.page.document}, стр. PDF {self.page.page}\n{self.page.text[:MAX_PAGE_CHARS]}"


def make_fragments(hits: list[Hit]) -> list[Fragment]:
    return [Fragment(f"S{i}", h) for i, h in enumerate(hits, 1)]


def quote_in_page(quote: str, page_text: str) -> bool:
    q = norm(quote)
    return len(q) >= 12 and q in norm(page_text)


def source_of(frag: Fragment, quote: str | None = None) -> SourcePage:
    return SourcePage(document=frag.page.document, page=frag.page.page, quote=(quote or "")[:300] or None)


@dataclass
class Extraction:
    coverage: str = "none"  # specific | general | none (after verification)
    risk: str = ""
    works: list[WorkItem] = field(default_factory=list)
    parts: list[PartItem] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # unverifiable LLM claims (for the trace)


EXTRACT_SYSTEM = """Ты помощник механика. Работаешь ТОЛЬКО с переданными фрагментами руководства по двигателю.
Правила: ничего не выдумывай; каждое действие и каждая запчасть должны подтверждаться ДОСЛОВНОЙ цитатой (12-200 символов подряд, как в тексте фрагмента) и меткой фрагмента S#.
Не используй знания вне фрагментов. Артикулов не придумывай: если артикула нет в тексте, пиши название как в тексте.
У каждой работы укажи scope: "specific" — действие из процедуры именно для найденной неисправности (описана по названию/признакам); "general" — действие из ОБЩЕЙ процедуры осмотра/проверки/дефектовки узла или агрегата, когда частной процедуры для этой неисправности нет. Работы из процедур других неисправностей (другой узел, другой симптом) не включай вообще. Работа — это практическое действие механика, а не пояснение.
Если частной процедуры нет, а общая есть — включай только общие (scope=general). Запчасти включай только если они названы в тексте для этой же процедуры.
Ответ строго JSON:
{"risk":"1-2 фразы о последствиях, только из фрагментов, иначе пусто",
 "risk_source":{"source":"S#","quote":"..."},
 "works":[{"title":"краткое действие","scope":"specific|general","source":"S#","quote":"дословно"}],
 "parts":[{"title":"запчасть","source":"S#","quote":"дословно"}]}"""


def extract_with_llm(llm, fault_title: str, symptoms: str, frags: list[Fragment]) -> tuple[Extraction, dict]:
    user = (
        f"Неисправность по акустической диагностике: {fault_title}\nПризнаки: {symptoms}\n\nФрагменты:\n\n"
        + "\n\n".join(f.prompt_text() for f in frags)
    )
    raw = llm.json("extract_works", EXTRACT_SYSTEM, user, max_tokens=3500)
    return verify_extraction(raw, frags), raw


def verify_extraction(raw: dict, frags: list[Fragment]) -> Extraction:
    by = {f.label: f for f in frags}
    ex = Extraction()

    def ok(item: dict) -> Fragment | None:
        f = by.get(str(item.get("source")))
        if f and quote_in_page(str(item.get("quote", "")), f.page.text):
            return f
        ex.dropped.append(str(item.get("title") or item.get("quote") or item)[:120])
        return None

    seen = set()
    for w in raw.get("works") or []:
        f = ok(w)
        if f and w.get("title") and w["title"] not in seen:
            seen.add(w["title"])
            ex.works.append(WorkItem(title=str(w["title"]).strip(), source=source_of(f, w["quote"])))
    seen = set()
    for p in raw.get("parts") or []:
        f = ok(p)
        if f and p.get("title") and p["title"] not in seen:
            seen.add(p["title"])
            ex.parts.append(PartItem(title=str(p["title"]).strip(), source=source_of(f, p["quote"])))
    rs = raw.get("risk_source") or {}
    f = by.get(str(rs.get("source")))
    if raw.get("risk") and f and quote_in_page(str(rs.get("quote", "")), f.page.text):
        ex.risk = str(raw["risk"]).strip()
    # coverage is derived in code from verified items, not trusted from the model
    scopes = {str(w.get("scope")) for w in (raw.get("works") or []) if any(x.title == w.get("title") for x in ex.works)}
    if "specific" in scopes:
        keep = "specific"
    elif "general" in scopes:
        keep = "general"
    else:
        keep = None
    if keep is None:
        ex.works, ex.parts, ex.coverage = [], [], "none"
        return ex
    ok_titles = {w["title"] for w in raw["works"] if w.get("scope") == keep}
    ex.works = [w for w in ex.works if w.title in ok_titles]
    pages = {(w.source.document, w.source.page) for w in ex.works}
    ex.parts = [p for p in ex.parts if p.source and (p.source.document, p.source.page) in pages]
    ex.coverage = keep
    return ex


# ---- deterministic fallback (no LLM): bullets under "Работы:" / "Запчасти:" ----

_BULLET = re.compile(r"^\s*[-•]\s*(.+)$")


def extract_fallback(fault_title: str, frags: list[Fragment]) -> Extraction:
    ex = Extraction()
    key = norm(fault_title)
    for f in frags:
        text = f.page.text
        if "работы" not in text.lower():
            continue
        section = None
        from ..retrieval.bm25 import tokens

        tt, pt = set(tokens(fault_title)), set(tokens(text))
        title_hit = bool(tt) and len(tt & pt) / len(tt) >= 0.6
        lines, cur = text.splitlines(), None
        items: dict[str, list[str]] = {"works": [], "parts": []}
        for ln in lines:
            low = ln.strip().lower()
            if low.startswith("работы"):
                section, cur = "works", None
                continue
            if low.startswith("запчасти"):
                section, cur = "parts", None
                continue
            m = _BULLET.match(ln)
            if section and m:
                items[section].append(m.group(1).strip()); cur = section
            elif section and cur and ln.strip() and not ln.strip().startswith("MOCK") and not ln.strip()[:1].isupper():
                items[cur][-1] += " " + ln.strip()
            elif section and not ln.strip():
                continue
            elif section:
                section = cur = None
        if items["works"] and title_hit:
            for t in items["works"]:
                ex.works.append(WorkItem(title=t, source=source_of(f, t)))
            for t in items["parts"]:
                ex.parts.append(PartItem(title=t, source=source_of(f, t)))
            ex.coverage = "specific"
            break
    return ex
