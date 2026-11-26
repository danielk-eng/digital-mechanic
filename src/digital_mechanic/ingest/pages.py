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
    joined = bool(left and right and right[0][0] - left[-1][2] < 7 and left[0][4][:1].isupper() and right[0][4][:1].islower())
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
    sym_pos = -1
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
                if re.match(r"^\d+\s+\S", st) or not syms:
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


def _extract_bbox_pages(pdf: Path) -> list[str]:
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", "-bbox-layout", str(pdf), "-"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return re.findall(r"<page [^>]*>(.*?)</page>", out, re.S)


def _pick(plain: str, reflowed: str | None) -> str:
    """Reflowed text only when it keeps the words (rotated or odd pages lose them: keep pdftotext)."""
    if not reflowed:
        return plain
    letters = lambda x: len(re.findall(r"\w", x))  # hyphen joins change word counts, not letters
    return reflowed if letters(reflowed) >= 0.97 * letters(plain) else plain


def extract_pdf(pdf: Path) -> list[str]:
    if shutil.which("pdftotext"):
        pages = _extract_pdftotext(pdf)
        try:
            boxes = _extract_bbox_pages(pdf)
        except (subprocess.CalledProcessError, OSError):
            boxes = []
        if len(boxes) == len(pages):
            out, g = [], None
            for t, b in zip(pages, boxes):
                tab, g = reflow_table(b, g)
                out.append(_pick(t, tab) if tab else _pick(t, reflow_side_captions(b)))
            pages = out
        return pages
    return _extract_pypdf(pdf)


def build_index(manuals_dir: str | Path, engine_docs_path: str | Path, out_path: str | Path) -> int:
    cfg = load_engine_docs(engine_docs_path)
    n = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for doc_id, meta in cfg["documents"].items():
            pdf = Path(manuals_dir) / meta["filename"]
            for i, text in enumerate(extract_pdf(pdf), start=1):
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
