"""Spoken-text captions duplicate the answer card, so they are an accessibility opt-in, not a default overlay."""
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/a11y.js"


def test_spoken_captions_are_opt_in():
    js = JS.read_text(encoding="utf-8")
    assert 'captions: () => localStorage.getItem("cc") === "1"' in js
    assert 'setCaptions(on) { localStorage.setItem("cc", on ? "1" : "0"); }' in js
