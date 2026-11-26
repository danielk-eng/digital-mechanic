"""Hard engine filter: never return a page from another engine's manual."""

from __future__ import annotations

from digital_mechanic.ingest.pages import ManualPage


def for_engine(pages: list[ManualPage], engine_type: str) -> list[ManualPage]:
    return [p for p in pages if engine_type in p.engine_types]
