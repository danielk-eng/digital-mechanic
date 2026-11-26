"""Section-aware view of the manuals: detect headings in page text, find the section (procedure) that
contains a cited line, and return its full text with the page span.

Heuristic headings (works for the engine RK/RE manuals and the extra truck manuals):
  * numbered: "9.3 Проверка зазора..." (single-number "3. Топливо" only if short); not a step sentence ending with "."
  * capitalised: "ГАЗОРАСПРЕДЕЛИТЕЛЬНЫЙ МЕХАНИЗМ"
Table-of-contents lines (dot leaders) are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .bm25 import tokens

NUM = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([А-ЯЁA-Z][^\n]{2,110})$")
LEADERS = re.compile(r"[.…_·]{4,}|…")
TAIL_WORDS = {"в", "и", "с", "на", "по", "к", "от", "до", "из", "для", "при", "или", "а", "не", "без", "за", "о", "об", "у"}
MAX_PAGES = 6
MAX_CHARS = 16000
MIN_CHARS = 900  # a clause shorter than this is shown with its parent section


@dataclass(frozen=True)
class Heading:
    page: int
    line: int
    level: int
    num: str
    title: str


@dataclass
class Section:
    doc_id: str
    num: str
    title: str
    level: int
    start_page: int
    end_page: int
    pages: list[dict] = field(default_factory=list)  # [{"page": n, "text": "..."}]
    focus_page: int = 0

    @property
    def text(self) -> str:
        return "\n".join(p["text"] for p in self.pages)

    @property
    def label(self) -> str:
        return f"{self.num} {self.title}".strip()

    @property
    def span(self) -> str:
        return f"{self.start_page}" if self.start_page == self.end_page else f"{self.start_page}–{self.end_page}"

    def as_dict(self) -> dict:
        return {"doc_id": self.doc_id, "num": self.num, "title": self.title, "label": self.label, "start_page": self.start_page,
                "end_page": self.end_page, "span": self.span, "pages": self.pages, "focus_page": self.focus_page}


def heading_of(line: str) -> tuple[int, str, str] | None:
    s = line.strip()
    if not 4 <= len(s) <= 120 or LEADERS.search(s):
        return None
    m = NUM.match(s)
    if m:
        num, title = m.group(1), m.group(2).strip()
        words = title.split()
        level = num.count(".") + 1
        if title.endswith((",", ";", "-", "–")) or words[-1].lower().rstrip(".") in TAIL_WORDS:
            return None
        if title.endswith(".") and (level == 1 or len(words) > 16):
            return None  # "1. Заглушить двигатель." is a step, not a heading
        if re.search(r"\d+[,.]\d+\s*(мм|мин|Н|кгс|%|л)\b", title) or re.search(r"\s{3,}", title):
            return None  # table rows with values / two-column layouts
        if level == 1 and len(words) > 6:
            return None
        if len(words) > 16:
            return None
        return level, num, title
    letters = [ch for ch in s if ch.isalpha()]
    words = s.split()
    if len(letters) >= 8 and sum(ch.isupper() for ch in letters) / len(letters) > 0.85 and 2 <= len(words) <= 12 \
            and not re.search(r"\d{3,}|[«»“”\"]", s) and not s.endswith((",", "-", "–", ".", ":")) \
            and words[-1].lower() not in TAIL_WORDS and sum(len(w) >= 3 for w in words) >= 2:
        return 1, "", s[0] + s[1:].lower()
    return None


MODULE = re.compile(r"(\d{3}-\d{2}-\d{6}-\d{3})")  # YaMZ-536 repair manual: one procedure module per code


def module_title(lines: list[str]) -> str:
    """Title under the running header 'Руководство по ремонту / двигателей ЯМЗ-536, ...'."""
    i = 0
    while i < len(lines) and i < 4 and (not lines[i].strip() or "Руководство" in lines[i] or "ЯМЗ-53" in lines[i]):
        i += 1
    out = []
    while i < len(lines) and lines[i].strip() and len(out) < 3 and not lines[i].strip().startswith("Порядок"):
        out.append(lines[i].strip()); i += 1
    return " ".join(out)[:150]


class SectionIndex:
    """pages: objects with doc_id, page, text (ManualPage or ExtraPage)."""

    def __init__(self, pages):
        self.text: dict[tuple[str, int], list[str]] = {}
        self.heads: dict[str, list[Heading]] = {}
        last_mod: dict[str, str] = {}
        for p in pages:
            lines = (p.text or "").split("\n")
            self.text[(p.doc_id, p.page)] = lines
            mod = next((m.group(1) for l in lines[-15:] if (m := MODULE.search(l))), None)
            if mod:
                if last_mod.get(p.doc_id) != mod:
                    self.heads.setdefault(p.doc_id, []).append(Heading(p.page, 0, 1, "", module_title(lines)))
                last_mod[p.doc_id] = mod
                continue
            hs = []
            caps = [bool(l.strip()) and heading_of(l) is not None and heading_of(l)[1] == "" for l in lines]
            for i, l in enumerate(lines):
                h = heading_of(l)
                if not h:
                    continue
                if h[1] == "" and ((i > 0 and (caps[i - 1] or lines[i - 1].strip())) or (i + 1 < len(lines) and caps[i + 1])):
                    continue  # block of capitals = warning text, not a heading
                hs.append(Heading(p.page, i, *h))
            if len(hs) > 18:  # table of contents / index page
                hs = []
            self.heads.setdefault(p.doc_id, []).extend(hs)
        for v in self.heads.values():
            v.sort(key=lambda h: (h.page, h.line))

    def locate(self, doc_id: str, page: int, hint: str = "") -> int:
        """Line index on the page that best matches the hint (quote or query); 0 if none."""
        lines = self.text.get((doc_id, page)) or []
        if not hint or not lines:
            return 0
        q = set(tokens(hint))
        if not q:
            return 0
        best, score = 0, 0.0
        for i in range(len(lines)):
            sc = 2 * len(q & set(tokens(lines[i]))) + len(q & set(tokens(" ".join(lines[i: i + 3]))))
            if sc > score:
                best, score = i, sc
        return best

    def find(self, doc_id: str, page: int, hint: str = "") -> Section | None:
        if (doc_id, page) not in self.text:
            return None
        line = self.locate(doc_id, page, hint)
        hs = self.heads.get(doc_id, [])
        start = None
        for h in hs:
            if (h.page, h.line) <= (page, line) and page - h.page <= MAX_PAGES - 1:
                start = h
            elif (h.page, h.line) > (page, line):
                break
        if start is None:
            start = Heading(page, 0, 9, "", "")
        sec = self._build(doc_id, page, line, start, hs)
        while start.level > 1 and len(sec.text) < MIN_CHARS:
            parent = next((h for h in reversed(hs) if (h.page, h.line) < (start.page, start.line) and h.level < start.level
                           and page - h.page <= MAX_PAGES - 1), None)
            if parent is None:
                break
            start = parent
            sec = self._build(doc_id, page, line, start, hs)
        return sec

    def _build(self, doc_id: str, page: int, line: int, start: Heading, hs: list[Heading]) -> Section:
        end_page, end_line = None, None
        for h in hs:
            if (h.page, h.line) > (start.page, start.line) and (h.page, h.line) > (page, line) and h.level <= start.level:
                end_page, end_line = h.page, h.line
                break
        if end_page is None or end_page - start.page >= MAX_PAGES:
            end_page, end_line = min(start.page + MAX_PAGES - 1, max(page + 1, start.page)), None
        # a deeper heading after the focus line can also close the section if nothing closer exists
        sec = Section(doc_id, start.num, start.title, start.level, start.page, start.page, focus_page=page)
        size = 0
        for pg in range(start.page, end_page + 1):
            lines = self.text.get((doc_id, pg))
            if lines is None:
                break
            lo = start.line if pg == start.page else 0
            hi = end_line if (pg == end_page and end_line is not None) else len(lines)
            chunk = "\n".join(lines[lo:hi]).strip()
            if pg > page and size + len(chunk) > MAX_CHARS:
                break
            if chunk or pg == page:
                sec.pages.append({"page": pg, "text": chunk})
                sec.end_page = pg
                size += len(chunk)
        return sec
