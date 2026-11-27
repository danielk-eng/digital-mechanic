"""Stylesheets parse as intended: braces balanced, no stray «}» (a misplaced brace on 26.11 23:27 dropped the phone rules for
the chief's panel and left the priority picker / defect-check styles inside a 420 px media query)."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static"


@pytest.mark.parametrize("name", sorted(p.name for p in STATIC.glob("*.css")))
def test_braces_balanced(name):
    s = re.sub(r"/\*.*?\*/", "", (STATIC / name).read_text(encoding="utf-8"), flags=re.S)
    s = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', "", s)
    depth = 0
    for ch in s:
        depth += ch == "{"
        depth -= ch == "}"
        assert depth >= 0, f"{name}: stray closing brace"
    assert depth == 0, f"{name}: {depth} unclosed block(s)"


def test_rules_outside_media_queries():
    s = re.sub(r"/\*.*?\*/", "", (STATIC / "app.css").read_text(encoding="utf-8"), flags=re.S)
    depth = 0
    for i, ch in enumerate(s):
        if s.startswith(".urg-label{", i) or s.startswith(".rec-text{", i):
            assert depth == 0
        depth += ch == "{"
        depth -= ch == "}"
