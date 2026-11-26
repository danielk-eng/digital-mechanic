"""One record per PDF page, 1-based page numbers, engine-bound document id."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from pypdf import PdfReader


@dataclass(frozen=True)
class ManualPage:
    document: str  # official title, goes into ticket references
    doc_id: str
    engine_types: tuple[str, ...]
    page: int  # PDF page number, starts at 1
    text: str
    needs_ocr: bool
    path: Path

    @property
    def key(self) -> str:
        return f"{self.doc_id}#{self.page}"


MIN_TEXT_CHARS = 40  # below this a page is treated as an image page


def load_engine_docs(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _ocr_page(pdf_path: Path, page: int) -> str:  # pragma: no cover - optional
    try:
        import subprocess, tempfile

        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["pdftoppm", "-r", "200", "-f", str(page), "-l", str(page), "-png", str(pdf_path), f"{d}/p"], check=True)
            img = next(Path(d).glob("p*.png"))
            out = subprocess.run(["tesseract", str(img), "-", "-l", "rus+eng"], capture_output=True, text=True, check=True)
            return out.stdout
    except Exception:
        return ""


def ingest_manuals(manuals_dir: str | Path, engine_docs_path: str | Path, cache_dir: str | Path | None = None, ocr: bool = False) -> list[ManualPage]:
    """Extract every page of every configured manual (text layer, optional OCR)."""
    cfg = load_engine_docs(engine_docs_path)
    pages: list[ManualPage] = []
    for doc_id, meta in cfg["documents"].items():
        pdf = Path(manuals_dir) / meta["filename"]
        if not pdf.exists():
            continue
        cache = None
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            cache = Path(cache_dir) / f"{doc_id}.{pdf.stat().st_size}.{int(pdf.stat().st_mtime)}.json"
            if cache.exists():
                for r in json.loads(cache.read_text(encoding="utf-8")):
                    pages.append(ManualPage(**{**r, "engine_types": tuple(r["engine_types"]), "path": pdf}))
                continue
        reader = PdfReader(str(pdf))
        doc_pages = []
        for i, pg in enumerate(reader.pages, start=1):
            text = (pg.extract_text() or "").strip()
            needs_ocr = len(text) < MIN_TEXT_CHARS
            if needs_ocr and ocr:
                text = _ocr_page(pdf, i) or text
            doc_pages.append(ManualPage(meta["title"], doc_id, tuple(meta["engines"]), i, text, needs_ocr, pdf))
        pages.extend(doc_pages)
        if cache:
            cache.write_text(json.dumps([{**{k: v for k, v in asdict(p).items() if k != "path"}} for p in doc_pages], ensure_ascii=False), encoding="utf-8")
    return pages
