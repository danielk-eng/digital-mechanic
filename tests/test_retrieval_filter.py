from pathlib import Path

from digital_mechanic.ingest.pages import ManualPage
from digital_mechanic.retrieval.filter import for_engine


def test_pages_from_another_engine_are_dropped():
    pages = [
        ManualPage("yamz-236-repair", "d1", ("ЯМЗ-236М2",), 10, "клапаны", False, Path("a.pdf")),
        ManualPage("yamz-536-repair", "d2", ("ЯМЗ-536",), 10, "турбо", False, Path("b.pdf")),
    ]
    got = for_engine(pages, "ЯМЗ-236М2")
    assert [p.document for p in got] == ["yamz-236-repair"]
