"""One record per PDF page, 1-based PDF page numbers, engine-bound document id.

Text is extracted once (pdftotext if installed, else pypdf) and cached in a gzip JSONL
index committed to the repo, so the running app does not need the PDFs to answer.
"""

from __future__ import annotations

import gzip
import json
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


def extract_pdf(pdf: Path) -> list[str]:
    if shutil.which("pdftotext"):
        return _extract_pdftotext(pdf)
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
