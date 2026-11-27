"""One record per PDF page, 1-based PDF page numbers, engine-bound document id.

Text is extracted once (pdftotext if installed, else pypdf) and cached in a gzip JSONL
index committed to the repo, so the running app does not need the PDFs to answer.
"""

from __future__ import annotations

import gzip
import html
from collections import Counter
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

MIN_TEXT_CHARS = 40  # below this a page is treated as an image page


@dataclass(frozen=True)
class ManualPage:
    document: str  # official designation from TZ section 3 -> ticket references[].manual
    doc_id: str
    engine_types: tuple[str, ...]
    page: int  # PDF page number, starts at 1
    text: str
    needs_ocr: bool = False
    filename: str = ""
    kind: str = ""

    @property
    def key(self) -> str:
        return f"{self.doc_id}#{self.page}"


def load_engine_docs(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _extract_pdftotext(pdf: Path) -> list[str]:
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


def _extract_pypdf(pdf: Path) -> list[str]:
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(str(pdf)).pages]


# ---------------------------------------------------------------- figure captions beside the text column
# On A5 manuals a figure and its caption often sit in one column and the procedure text in the other.
# pdftotext orders lines by height across both columns, so caption lines end up inside the text
# ("вставить болт 2 в отверРис. 28. Крепление генератора стие передней лапы ..."). We re-read such pages
# with word boxes, pull the side caption out of the flow and append it as its own paragraph.
CAPTION_RE = re.compile(r"^(Рис\.|Рисунок|Fig\.)\s*\d")
_WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
GAP_SPLIT = 14.0  # pt between words that starts a new segment (another column)


@dataclass
class _Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    words: list

    @property
    def text(self) -> str:
        out, prev = "", None
        for x0, x1, w in self.words:
            out += ("" if prev is not None and x0 - prev < 0.6 else (" " if out else "")) + w
            prev = x1
        return out


def _segments(page_xml: str) -> list[_Seg]:
    segs: list[_Seg] = []
    for line in re.findall(r"<line [^>]*>(.*?)</line>", page_xml, re.S):
        cur = None
        for m in _WORD_RE.finditer(line):
            x0, y0, x1, y1 = map(float, m.groups()[:4])
            w = html.unescape(m.group(5))
            if cur is None or x0 - cur.x1 > GAP_SPLIT:
                cur = _Seg(x0, y0, x1, y1, [])
                segs.append(cur)
            cur.words.append((x0, x1, w))
            cur.x1, cur.y0, cur.y1 = max(cur.x1, x1), min(cur.y0, y0), max(cur.y1, y1)
    return segs


def _beside(a: _Seg, others: list[_Seg]) -> list[_Seg]:
    """Segments of another column that share height with a (horizontally disjoint, vertically overlapping)."""
    return [o for o in others if o is not a and (o.x0 > a.x1 + 4 or o.x1 < a.x0 - 4) and o.y0 < a.y1 + 30 and o.y1 > a.y0 - 30]


def reflow_side_captions(page_xml: str) -> str | None:
    """Text of the page with side captions moved out of the text flow, or None when the page has none."""
    segs = sorted(_segments(page_xml), key=lambda s: (round(s.y0), s.x0))
    captions: list[list[_Seg]] = []
    taken: set[int] = set()
    for i, s in enumerate(segs):
        if id(s) in taken or not CAPTION_RE.match(s.text):
            continue
        side = _beside(s, segs)
        if not side:
            continue
        left = side[0].x0 > s.x1  # caption column is left of the text column
        border = min(o.x0 for o in side) - 2 if left else max(o.x1 for o in side) + 2
        block, last = [s], s
        for t in segs[i + 1:]:
            if t.y0 - last.y1 > 9:
                break
            inside = (t.x1 <= border and t.x0 >= s.x0 - 14) if left else (t.x0 >= border and t.x1 <= s.x1 + 40)
            if inside and id(t) not in taken:
                block.append(t)
                last = t
        captions.append(block)
        taken.update(id(t) for t in block)
    if not captions:
        return None
    lines: list[list[_Seg]] = []
    for s in segs:
        if id(s) in taken:
            continue
        if lines and abs(lines[-1][0].y0 - s.y0) < 2.5:
            lines[-1].append(s)
        else:
            lines.append([s])
    body = "\n".join(" ".join(t.text for t in sorted(ln, key=lambda t: t.x0)) for ln in lines)
    return body + "".join("\n\n" + "\n".join(t.text for t in b) for b in captions)


# ---------------------------------------------------------------- «Причина | Способ устранения» tables
# pdftotext reads such tables column by column (all causes, then all remedies) or mixes the cell lines, so a cause
# and its remedy end up apart. We rebuild the table row by row: «cause — remedy», hyphenation joined inside cells.
TABLE_LEFT_RE = re.compile(r"^(Вероятн\w+ |Возможн\w+ )?(Причин|Неисправност)\w*", re.I)
TABLE_RIGHT_RE = re.compile(r"(Способ|Метод)\w* устранения", re.I)
ROW_START_RE = re.compile(r"^([А-ЯЁA-Z]|\d+(\.\s?\d+)+\.?\s|\d+\.\s|[а-я]\)(\s|$)|[-–—]\s|\*)")  # new cause: capital, «5.22 », «3. », «а) », «- », footnote «*»
MAX_CELL = 700


def _words(page_xml: str) -> list[list[tuple]]:
    """Lines of words (x0, y0, x1, y1, text), merged when pdftotext split one visual line into several."""
    lines: list[list[tuple]] = []
    for line in re.findall(r"<line [^>]*>(.*?)</line>", page_xml, re.S):
        ws = [(*map(float, m.groups()[:4]), html.unescape(m.group(5))) for m in _WORD_RE.finditer(line)]
        if ws:
            lines.append(ws)
    lines.sort(key=lambda ws: min(w[1] for w in ws))
    out: list[list[tuple]] = []
    for ws in lines:
        if out and abs(min(w[1] for w in out[-1]) - min(w[1] for w in ws)) < 2.5:
            out[-1].extend(ws)
        else:
            out.append(list(ws))
    return [sorted(ws, key=lambda w: w[0]) for ws in out]


def _join_words(ws: list[tuple]) -> str:
    return _Seg(0, 0, 0, 0, [(w[0], w[2], w[4]) for w in ws]).text


def _join_cell(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln
        else:
            out = (out + " " + ln) if out else ln
    return out


def _gutter(lines: list[list[tuple]], lo: float, hi: float) -> float | None:
    """x between lo and hi crossed by the fewest words: the middle of the widest such run (a short word near
    the header gap, «давления в», must not pull the gutter into the left column)."""
    if hi - lo < 4:
        return None
    runs: list[list[float]] = []  # [count, start, end]
    x = lo
    while x <= hi:
        n = sum(1 for ws in lines for w in ws if w[0] < x < w[2])
        if runs and runs[-1][0] == n and abs(x - runs[-1][2] - 1) < 1.01:
            runs[-1][2] = x
        else:
            runs.append([n, x, x])
        x += 1
    if not runs:
        return None
    m = min(r[0] for r in runs)
    best = max((r for r in runs if r[0] == m), key=lambda r: r[2] - r[1])
    total = sum(len(ws) for ws in lines) or 1
    return (best[1] + best[2]) / 2 if m <= 0.05 * total else None


def _split(ws: list[tuple], g: float) -> tuple[list[tuple], list[tuple], bool]:
    """Left/right words of a table line; phrase=True when the line is one phrase across the gutter (sub-heading)."""
    left = [w for w in ws if (w[0] + w[2]) / 2 < g]
    right = [w for w in ws if (w[0] + w[2]) / 2 >= g]
    crossing = any(w[0] < g - 1 < g + 1 < w[2] for w in ws)
    # a centred sub-heading reads on across the gutter («Двигатель не развивает | мощности, дымит»), a row does not
    # a numbered centred heading too: «1. Двигатель | не пускается», «6.1 Недостаточная | подача топлива» (ЗМЗ-409 РК table 5)
    cap = bool(left) and (left[0][4][:1].isupper() or bool(re.fullmatch(r"\d+(\.\d+)*\.?", left[0][4])) and len(left) > 1 and left[1][4][:1].isupper())
    joined = bool(left and right and right[0][0] - left[-1][2] < 7 and cap and right[0][4][:1].islower())
    lone_number = not right and len(left) == 1 and left[0][4].isdigit() or not left and len(right) == 1 and right[0][4].isdigit()
    return left, right, crossing or joined or lone_number


def reflow_table(page_xml: str, prev_gutter: float | None = None) -> tuple[str | None, float | None]:
    """(row-wise text, gutter) for a cause/remedy table page; (None, None) when the page is not such a table.
    prev_gutter: the table continues from the previous page without a header."""
    lines = _words(page_xml)
    if not lines:
        return None, None
    head_i, g, hx0 = None, None, None
    for i, ws in enumerate(lines):
        t = _join_words(ws)
        if len(t) > 90:
            continue
        segs: list[list[tuple]] = []
        for w in ws:
            if segs and w[0] - segs[-1][-1][2] < GAP_SPLIT:
                segs[-1].append(w)
            else:
                segs.append([w])
        if len(segs) == 2 and TABLE_LEFT_RE.match(_join_words(segs[0])) and TABLE_RIGHT_RE.search(_join_words(segs[1])):
            body = lines[i + 1:]
            g = _gutter(body, segs[0][-1][2], segs[1][0][0])
            if g is None:  # the table ends mid-page and full-width text follows: the gutter of its first rows
                g = _gutter(body[:12], segs[0][-1][2], segs[1][0][0])
            if g is not None:
                head_i, hx0 = i, segs[0][0][0]
                break
    if head_i is None:
        if prev_gutter is None:
            return None, None
        body = lines[1:-1] if len(lines) > 3 else lines
        split = [_split(ws, prev_gutter) for ws in body]
        both = sum(1 for l, r, ph in split if l and r and not ph)
        clean = sum(1 for l, r, ph in split if not ph)
        lefts = [l for l, r, ph in split if l and not ph]
        if not body or clean < 0.8 * len(body) or both < max(3, 0.3 * len(body)) or len(lefts) < 3:
            return None, None
        g, head_i = prev_gutter, -1
    out = [_join_words(ws) for ws in lines[:head_i + 1]]
    if head_i >= 0:
        out[-1] = " — ".join(_join_words(x) for x in ([w for w in lines[head_i] if w[2] < g], [w for w in lines[head_i] if w[0] > g]))
    rows: list[list[list[str]]] = []

    long_cells = []
    sub_of: dict[int, list] = {}  # dash sub-item row -> the row whose remedy runs beside it

    def flush() -> None:
        for lc, rc in rows:
            L, R = _join_cell(lc), _join_cell(rc)
            long_cells.extend(x for x in (L, R) if len(x) > MAX_CELL)
            out.append(f"{L} — {R}" if L and R else (L or R))
        rows.clear()

    # three columns «Неисправность и ее признаки | Вероятная причина | Способ устранения»: the symptom column
    # left of the cause column becomes a line of its own before its causes
    g1, body = None, lines[head_i + 1:]
    if hx0 is not None:
        xmin = min(ws[0][0] for ws in body) if body else hx0
        near = [w for ws in lines[max(0, head_i - 2):head_i + 3] for w in ws]
        if hx0 - xmin > 60 and any(w[4].startswith("Неисправност") and w[2] < hx0 for w in near):
            top = [ws for ws in body[:12] if not any(w[0] < g - 1 < g + 1 < w[2] for w in ws)]  # rows, not prose
            c = _gutter(top, xmin + 20, hx0 - 1)
            if c is not None and sum(1 for ws in top if any(w[2] < c for w in ws) and any(c < w[0] and w[2] < g for w in ws)) >= 3:
                g1 = c
    syms: list[list[str]] = []
    sym_pos, hdr_joined = -1, False
    tail: list[str] = []
    def full_width(ws: list[tuple]) -> bool:
        return len(_join_words(ws)) > 50 and any(w[0] < g - 1 < g + 1 < w[2] for w in ws)

    rx_count = Counter(round(r[0][0]) for r in (([w for w in ws if w[0] >= g]) for ws in body) if r)
    rx = rx_count.most_common(1)[0][0] if rx_count else None

    def table_goes_on(k: int) -> bool:
        """A later line is a table row: it starts at a column start of the rows above, has a word exactly at the
        remedy column and none across the gutter (justified prose after the table does not line up so)."""
        at_rx = [ws for ws in body if any(abs(w[0] - rx) < 1.5 for w in ws)] if rx is not None else []
        starts = [x for x, _ in Counter(round(ws[0][0]) for ws in at_rx).most_common(2)]
        return rx is not None and any(any(abs(ws[0][0] - x) < 1.5 for x in starts) and any(abs(w[0] - rx) < 1.5 for w in ws)
                                      and not any(w[0] < g - 1 < g + 1 < w[2] for w in ws) for ws in body[k + 1:])

    for k, ws in enumerate(body):
        if tail:
            tail.append(_join_words(ws))
            continue
        if full_width(ws) and k + 1 < len(body) and len(_join_words(body[k + 1])) > 40 and not table_goes_on(k):  # a paragraph
            flush()
            tail.append(_join_words(ws))  # a full-width paragraph: the table is over, the rest is plain text
            continue
        if g1 is not None:
            sym = [w for w in ws if w[2] < g1 + 1]
            if sym:
                st = _join_words(sym)
                numbered = bool(re.match(r"^\d+\s+\S", st))
                if not syms and not numbered and not hdr_joined and head_i >= 1 and all(w[2] < g1 + 1 for w in lines[head_i - 1]):
                    # «Неисправность и ее | признаки» around the header line: one header «… признаки — причина — способ»
                    out[head_i - 1] = _join_cell([out[head_i - 1], st]) + " — " + out[head_i]
                    del out[head_i]
                    hdr_joined = True
                    ws = [w for w in ws if w[2] >= g1 + 1]
                    if not ws:
                        continue
                    sym = []
            if sym:
                if numbered or not syms:
                    flush()
                    syms.append([st])
                    sym_pos = len(out)
                    out.append("")  # the symptom line goes here, filled as its cell grows
                else:
                    syms[-1].append(st)
                out[sym_pos] = _join_cell(syms[-1])
                ws = [w for w in ws if w[2] >= g1 + 1]
                if not ws:
                    continue
        left, right, phrase = _split(ws, g)
        if phrase or not (left or right):
            flush()
            out.append(_join_words(ws))  # sub-heading across the table, page number, notes
            continue
        L, R = _join_words(left), _join_words(right)
        cur = rows[-1] if rows else None
        prev_l = (cur[0][-1] if cur and cur[0] else "")
        new = bool(ROW_START_RE.match(L)) and not prev_l.endswith("-")
        owner = next((r for r in reversed(rows) if r[1]), None)
        last_r = owner[1][-1] if owner else ""
        if L[:1] == "*" and k + 1 < len(body) and any(w[0] < g - 1 < g + 1 < w[2] for w in body[k + 1]):
            flush()
            out.append(_join_words(ws))  # a footnote running across both columns under the table
            continue
        if new and owner is not None and L[:1] in "-–—*" and R:
            steps = re.findall(r"(?:^|[.;] )(\d+) [А-ЯЁ]", " ".join(owner[1]))
            m = re.match(r"(\d+) [А-ЯЁ]", R)
            if R[:1].islower() or (m and steps and int(m.group(1)) == int(steps[-1]) + 1):
                # a dash sub-item (or a footnote) beside the running remedy of the item above: the item is its
                # own line, the remedy words stay with the remedy they continue
                owner[1].append(R)
                rows.append([[L], []])
                sub_of[id(rows[-1])] = owner
                continue
        if new and R and L[:1] not in "-–—*" and R[:1].islower() and last_r.endswith(","):
            new = False  # «ЭБУ из-за …» beside «…из разъема ЭБУ, | заменить жгут»: one row going on
        if cur is None or new:
            rows.append([[L] if L else [], [R] if R else []])
        else:
            if L:
                cur[0].append(L)
            if R:
                (sub_of[id(cur)] if id(cur) in sub_of else cur)[1].append(R)
    flush()
    out.extend(tail)
    if long_cells:  # rows ran together (three-column table, list items): keep pdftotext's text
        return None, g
    return "\n".join(x for x in out if x.strip()), g


# ---------------------------------------------------------------- repair-manual defect maps (ЯМЗ-536 РК «Карта дефектации»)
# Columns «№ поз. | Возможный дефект, способ установления | Размеры: Номинальные | Допустимые | Заключение». pdftotext reads
# them column by column, so «Не допускается»/«Браковать» and the sizes end up apart from their defect. The row number and the
# one-line cells are vertically centred on a row: a row runs from its first defect line to 2*y(number) - start.
_FOOT_RE = re.compile(r"^(Действительно|\d{4}-\d{2}-\d{2}\s+Страница)")


def reflow_defect_map(page_xml: str) -> str | None:
    lines = _words(page_xml)
    hi = next((i for i, ws in enumerate(lines) if {"Номинальные", "Допустимые"} <= {w[4] for w in ws}), None)
    if hi is None or not any(w[4] == "№" for ws in lines[max(0, hi - 6):hi] for w in ws):
        return None
    head = lines[:hi + 1]
    x_nom = next(w[0] for w in lines[hi] if w[4] == "Номинальные")
    x_all = next(w[0] for w in lines[hi] if w[4] == "Допустимые")
    concl = [w[0] for ws in head[-6:] for w in ws if w[4] in ("Заключение", "Метод")]
    x_num = next((w[2] for ws in head[-6:] for w in ws if w[4] in ("№", "поз.")), None)
    if not concl or x_num is None:
        return None
    x_con = min(concl) - 8
    body, tail = [], []
    for ws in lines[hi + 1:]:
        (tail if tail or _FOOT_RE.match(_join_words(ws)) else body).append(ws)
    nums = [(w[1], w[4]) for ws in body for w in ws if w[2] <= x_num + 12 and re.fullmatch(r"\d{1,2}", w[4])]
    if not nums:
        return None
    rest = [[w for w in ws if not (w[2] <= x_num + 12 and re.fullmatch(r"\d{1,2}", w[4]))] for ws in body]
    rest = [ws for ws in rest if ws]
    rows, k, start = [], 0, None
    for n, (y, label) in enumerate(nums):
        if k >= len(rest):
            break
        start = rest[k][0][1]
        end = 2 * y - start + 4 if n < len(nums) - 1 else float("inf")
        cur = []
        while k < len(rest) and rest[k][0][1] <= end:
            cur.append(rest[k]); k += 1
        rows.append((label, cur))
    if k < len(rest):
        return None  # lines left over: the layout is not what we expect, keep pdftotext's text
    out = [_join_words(ws) for ws in head[:-1]]  # title, part, material (header labels of the size columns dropped)
    out.append("№ — Возможный дефект, способ установления — Размеры, мм: номинальные / допустимые — Заключение")
    for label, cur in rows:
        cols = {"d": [], "n": [], "a": [], "c": []}
        for ws in cur:
            parts = {"d": [], "n": [], "a": [], "c": []}
            for w in ws:
                c = "c" if w[0] >= x_con else "a" if w[0] >= x_all - 12 else "n" if w[0] >= x_nom - 15 else "d"
                parts[c].append(w)
            for c, p in parts.items():
                if p:
                    cols[c].append(_join_words(p))
        d, nm, al, cn = (_join_cell(cols[c]) for c in "dnac")
        if nm and not al and nm.lower().startswith("не допуска"):
            sizes = nm[0].lower() + nm[1:]
        else:
            sizes = "; ".join(x for x in (f"номинальный {nm}" if nm else "", f"допустимый {al}" if al else "") if x)
        out.append(f"{label}. {d}" + (f" — {sizes}" if sizes else "") + (f" — {cn}" if cn else ""))
    out.extend(_join_words(ws) for ws in tail)
    return "\n".join(x for x in out if x.strip())


def reflow_defect_map_pos(page_xml: str) -> str | None:
    """Defect map variant with an «Обозначение» size column and position groups («Поз. 1,3,4,5,6») instead of row numbers
    (ЯМЗ-536 РК с. 243, crankshaft): one defect -> several size grades (Н, Ш1…Ш5, К1…К5), each with its own nominal / allowed /
    conclusion. The «Поз.» block is vertically centred on its defect, like the row numbers of reflow_defect_map."""
    lines = _words(page_xml)
    hi = next((i for i, ws in enumerate(lines) if {"Номинальные", "Допустимые"} <= {w[4] for w in ws}), None)
    if hi is None or not any(w[4] == "№" for ws in lines[max(0, hi - 6):hi] for w in ws):
        return None
    x_nom = next(w[0] for w in lines[hi] if w[4] == "Номинальные")
    x_all = next(w[0] for w in lines[hi] if w[4] == "Допустимые")
    concl = [w[0] for ws in lines[max(0, hi - 6):hi + 1] for w in ws if w[4] in ("Заключение", "Метод")]
    x_def = next((w[0] for ws in lines[max(0, hi - 6):hi + 1] for w in ws if w[4] in ("Возможный", "Способ", "Средства")), None)
    if not concl or x_def is None:
        return None
    x_con, x_d = min(concl) - 8, x_def - 18  # defect column starts a little left of its centred header
    body, tail = [], []
    for ws in lines[hi + 1:]:
        (tail if tail or _FOOT_RE.match(_join_words(ws)) else body).append(ws)
    pos_w = [(w[1], w[4]) for ws in body for w in ws if w[2] < x_d]
    if not pos_w or pos_w[0][1] != "Поз.":
        return None
    blocks = []  # [y_first, y_last, label]
    for y, tx in pos_w:
        if tx == "Поз." or not blocks:
            blocks.append([y, y, ""])
        else:
            blocks[-1][1] = y; blocks[-1][2] += tx
    rest = [[w for w in ws if w[2] >= x_d] for ws in body]
    rest = [ws for ws in rest if ws]
    if len(blocks) < 1 or not rest:
        return None
    groups, k = [], 0
    for n, (y0, y1, label) in enumerate(blocks):
        if k >= len(rest):
            return None
        start = rest[k][0][1]
        end = 2 * ((y0 + y1) / 2) - start + 4 if n < len(blocks) - 1 else float("inf")
        cur = []
        while k < len(rest) and rest[k][0][1] <= end:
            cur.append(rest[k]); k += 1
        groups.append((label.strip(",") , cur))
    if k < len(rest):
        return None
    out = [_join_words(ws) for ws in lines[:hi - 4] if ws]  # title, part, material
    out.append("Поз. — Возможный дефект, способ установления — Обозначение: номинальный / допустимый размер, мм — Заключение")
    for label, cur in groups:
        d, grades = [], []
        for ws in cur:
            dw = [w for w in ws if w[0] < x_nom - 12 and w[0] < 245]
            sw = [w for w in ws if w not in dw]
            if dw:
                d.append(_join_words(dw))
            if sw:
                des = [w for w in sw if w[0] < x_nom + 15 and re.fullmatch(r"[НШК]\d?", w[4])]
                nm = [w for w in sw if w not in des and w[0] < x_all - 12]
                al = [w for w in sw if x_all - 12 <= w[0] < x_con]
                cn = [w for w in sw if w[0] >= x_con]
                nm_t = re.sub(r"\s*-\s*", "-", _join_words(nm)) if nm else ""
                g = (_join_words(des) + ": " if des else "")
                if nm_t.lower().startswith("не допуска"):
                    g += nm_t[0].lower() + nm_t[1:]
                else:
                    g += "; ".join(x for x in (f"номинальный {nm_t}" if nm_t else "", f"допустимый {_join_words(al)}" if al else "") if x)
                if cn:
                    g += (" — " if g else "") + _join_words(cn)
                grades.append(g)
        out.append(f"Поз. {label}. {_join_cell(d)}" + (" — " + "; ".join(grades) if grades else ""))
    out.extend(_join_words(ws) for ws in tail)
    return "\n".join(x for x in out if x.strip())


# ---------------------------------------------------------------- numbered fault index (ЯМЗ-536 РЭ «Раздел | Внешнее проявление неисправности»)
# pdftotext prints the section numbers as one column and the (multi-line) titles after them. The number is vertically centred
# on its title, so every title line goes to the nearest number.
def reflow_numbered_index(page_xml: str) -> str | None:
    lines = _words(page_xml)
    hi = next((i for i, ws in enumerate(lines) if {"Раздел", "Внешнее"} <= {w[4] for w in ws}), None)
    if hi is None:
        return None
    x_t = next(w[2] for w in lines[hi] if w[4] == "Раздел") + 1  # titles start right of the «Раздел» header
    nums, tl = [], []
    for ws in lines[hi + 1:]:
        for w in ws:
            if w[2] < x_t and re.fullmatch(r"\d{1,2}", w[4]):
                nums.append(((w[1] + w[3]) / 2, w[4]))
        t = [w for w in ws if w[0] >= x_t]
        if t:
            tl.append(((min(w[1] for w in t) + max(w[3] for w in t)) / 2, t))
    if len(nums) < 5:
        return None
    rows = {n: [] for _, n in nums}
    tail = []
    for y, t in tl:
        dy, n = min((abs(y - ny), n) for ny, n in nums)
        (rows[n] if dy <= 12 else tail).append(_join_words(t))
    if any(not v for v in rows.values()):
        return None
    out = [_join_words(ws) for ws in lines[:hi]]
    out.append("Раздел — Внешнее проявление неисправности")
    out += [f"{n}. " + re.sub(r"(?<=[А-ЯЁ])- (?=[А-ЯЁ])", "", _join_cell(rows[n])) for _, n in nums]  # «НОМИ- НАЛЬНОЙ»
    rest = [_join_words([w for w in ws if not (w[2] < x_t and re.fullmatch(r"\d{1,2}", w[4]))]) for ws in lines[hi + 1:]]
    out += [r for r in tail + rest if re.fullmatch(r"\d{1,3}", r.strip()) and r not in out][:1]  # page number
    return "\n".join(x for x in out if x.strip())


def _xml_lines(page_xml: str) -> list[list]:
    out = []
    for m in re.finditer(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', page_xml, re.S):
        t = " ".join(html.unescape(w) for w in re.findall(r"<word[^>]*>(.*?)</word>", m.group(5))).strip()
        if t:
            out.append([float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), t])
    return out


def _short_line_share(plain: str) -> float:
    ls = [l for l in plain.split("\n") if l.strip()]
    return sum(len(l.strip()) < 22 for l in ls) / len(ls) if len(ls) >= 15 else 0.0


def _col_segments(items: list[list]) -> list[str]:
    """Lines of one table row (or the header) grouped per column, left to right: lines whose x-ranges overlap are one
    cell (wrapped and centred header lines «Предельно-допу- / стимый / размер, мм»), read top to bottom."""
    cols: list[list] = []  # [x0, x1, lines]
    for v in sorted(items, key=lambda b: b[0]):
        for c in cols:
            if v[0] < c[1] - 2 and v[2] > c[0] + 2:
                c[0], c[1] = min(c[0], v[0]), max(c[1], v[2])
                c[2].append(v)
                break
        else:
            cols.append([v[0], v[2], [v]])
    cols.sort(key=lambda c: c[0])
    return [_join_cell([v[4] for v in sorted(c[2], key=lambda b: b[1])]) for c in cols]


def reflow_grid(page_xml: str) -> str | None:
    """Dimension/spec tables in 3+ columns (ЗМЗ-409 РК p. 124, ЗМЗ-5234 РК p. 151–156): pdftotext gives them
    column by column (all names, then all sizes). Rows are rebuilt from the first column: a cell starts with a capital
    or digit, a lowercase line just under it continues it; every other line belongs to the row whose first cell starts
    above its middle. Output «name — value | value | …», header columns first. None when it does not look like such a table."""
    L = _xml_lines(page_xml)
    if len(L) < 15:
        return None
    ys = sorted(l[1] for l in L)
    L = [l for l in L if not (re.fullmatch(r"\d{1,3}", l[4]) and (l[1] >= ys[-1] - 1 or l[1] <= ys[0] + 1))]  # page number
    x0 = min(l[0] for l in L)
    left = sorted((l for l in L if l[0] < x0 + 20), key=lambda l: l[1])
    cells: list[list] = []
    sub = False  # under «Диаметр шеек …:» every lowercase line («передней», «задней») is its own sub-row
    for l in left:
        cont = bool(cells) and (l[4][:1].islower() or l[4][:1] in "(–-") and l[1] - cells[-1][3] < 6
        if cont and not sub and not cells[-1][4][-1].endswith(":"):
            cells[-1][3] = l[3]
            cells[-1][4].append(l[4])
        else:
            if cells and cells[-1][4][-1].endswith(":"):
                sub = True
            elif not l[4][:1].islower():
                sub = False
            cells.append([l[0], l[1], l[2], l[3], [l[4]]])
    if len(cells) < 3:
        return None
    rows: list[list] = [[c, []] for c in cells]
    head: list[list] = []
    left_ids = {id(l) for l in left}
    for l in L:
        if id(l) in left_ids:
            continue
        yc = (l[1] + l[3]) / 2
        ks = [i for i, c in enumerate(cells) if c[1] - 3 <= yc]
        (rows[ks[-1]][1] if ks else head).append(l)
    out = [" | ".join(_col_segments(head))] if head else []
    filled = wide = numeric = 0
    for c, vals in rows:
        segs = _col_segments(vals)
        filled += bool(segs)
        wide += len(segs) > 16
        name = _join_cell(c[4])
        numeric += bool(re.fullmatch(r"[\d\s]+", name) or name.startswith("("))
        out.append(name + (" — " + " | ".join(segs) if segs else ""))
    # a real table: most rows have values, rows are not a whole page of cells, names are words (not figure callouts)
    if filled < 3 or filled < 0.6 * len(rows) or wide or numeric > 0.2 * len(rows):
        return None
    return "\n".join(out)


def _extract_bbox_pages(pdf: Path) -> list[str]:
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", "-bbox-layout", str(pdf), "-"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return re.findall(r"<page [^>]*>(.*?)</page>", out, re.S)


def _pick(plain: str, reflowed: str | None) -> str:
    """Reflowed text only when it keeps the words (rotated or odd pages lose them: keep pdftotext)."""
    if not reflowed:
        return plain
    letters = lambda x: len(re.findall(r"\w", x))  # hyphen joins change word counts, not letters
    return reflowed if letters(reflowed) >= 0.97 * letters(plain) else plain


def _is_table_head(line: str) -> bool:
    return " — " in line and bool(TABLE_LEFT_RE.match(line)) and bool(TABLE_RIGHT_RE.search(line))


def _last_heading(tab: str) -> str | None:
    """The last symptom heading of a cause/remedy table page (consecutive lines without « — » after the header)."""
    lines = [l.strip() for l in tab.split("\n")]
    start = next((i + 1 for i, l in enumerate(lines) if _is_table_head(l)), 0)
    head, cur, prev_heading = None, [], False
    for l in lines[start:]:
        if not l or re.fullmatch(r"\d{1,4}", l) or l.startswith("*"):
            continue
        if " — " in l:
            if cur:
                head = " ".join(cur)
            cur, prev_heading = [], False
        else:
            cur = (cur if prev_heading else []) + [l]
            prev_heading = True
    if cur:
        head = " ".join(cur)
    head = re.sub(r"(\s*\(продолжение\))+$", "", head or "")
    # a symptom, not a wrapped cell fragment: starts with a capital or a number, no «…)» closing an earlier line
    if not head or len(head) > 140 or not re.match(r"[А-ЯЁA-Z0-9]", head) or re.match(r"[^(]*\)", head) and not re.match(r"\d+\)", head):
        return None
    return head


def _continue_heading(tab: str, prev_head: str | None) -> str:
    """Rows at the top of a table page belong to the last symptom of the previous page: name it there
    («Износ поршневых колец» on ЯМЗ-236 РЭ p. 53 read as a cause of «Двигатель стучит»)."""
    if not prev_head:
        return tab
    lines = tab.split("\n")
    i = next((k for k, l in enumerate(lines) if _is_table_head(l.strip())), -1)
    nxt = next((l for l in lines[i + 1:] if l.strip() and not re.fullmatch(r"\s*\d{1,4}\s*", l)), "")
    if " — " not in nxt or _is_table_head(nxt):
        return tab
    return "\n".join(lines[:i + 1] + [f"{prev_head} (продолжение)"] + lines[i + 1:])


def extract_pdf(pdf: Path, grid_pages: frozenset[int] = frozenset()) -> list[str]:
    if shutil.which("pdftotext"):
        pages = _extract_pdftotext(pdf)
        try:
            boxes = _extract_bbox_pages(pdf)
        except (subprocess.CalledProcessError, OSError):
            boxes = []
        if len(boxes) == len(pages):
            out, g, head = [], None, None
            for t, b in zip(pages, boxes):
                was_table = g is not None
                tab, g = reflow_table(b, g)
                if tab and was_table:
                    tab = _continue_heading(tab, head)
                dm = None if tab else (reflow_defect_map(b) or reflow_defect_map_pos(b) or reflow_numbered_index(b))
                if len(out) + 1 in grid_pages and not tab and not dm and _short_line_share(t) >= 0.75:
                    dm = reflow_grid(b)
                out.append(_pick(t, tab) if tab else _pick(t, dm) if dm else _pick(t, reflow_side_captions(b)))
                head = _last_heading(tab) if tab and out[-1] == tab else None
            pages = out
        return pages
    return _extract_pypdf(pdf)


def build_index(manuals_dir: str | Path, engine_docs_path: str | Path, out_path: str | Path) -> int:
    cfg = load_engine_docs(engine_docs_path)
    n = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for doc_id, meta in cfg["documents"].items():
            pdf = Path(manuals_dir) / meta["filename"]
            for i, text in enumerate(extract_pdf(pdf, frozenset(meta.get("grid_tables") or ())), start=1):
                text = text.strip()
                rec = ManualPage(meta["title"], doc_id, tuple(meta["engines"]), i, text, len(text) < MIN_TEXT_CHARS, meta["filename"], meta.get("kind", ""))
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
                n += 1
    return n


def load_pages(index_path: str | Path, engine_docs_path: str | Path, manuals_dir: str | Path | None = None) -> list[ManualPage]:
    """Load the page index; rebuild it from PDFs if missing and the PDFs are present."""
    p = Path(index_path)
    if not p.exists():
        if manuals_dir is None:
            raise FileNotFoundError(index_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        build_index(manuals_dir, engine_docs_path, p)
    cfg = load_engine_docs(engine_docs_path)["documents"]
    pages = []
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            meta = cfg.get(r["doc_id"])
            if not meta:
                continue
            r["engine_types"] = tuple(meta["engines"])  # yaml is the source of truth
            r["document"] = meta["title"]
            pages.append(ManualPage(**r))
    return pages
