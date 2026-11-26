"""Contract: one record per PDF page, 1-based page numbers, engine-bound doc id."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManualPage:
    document: str
    engine_types: tuple[str, ...]
    page: int
    text: str
    needs_ocr: bool
    path: Path
