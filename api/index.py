"""Vercel entry point (reduced backup stand): official mocks in-process, SQLite in /tmp, no PDF page images.

Two layouts: the repo itself (local test) or a single bundle.tar.gz next to this file (deployment payload),
extracted to /tmp on cold start.
"""
import os
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE / "bundle.tar.gz"
if BUNDLE.exists():
    ROOT = Path("/tmp/dmapp")
    if not (ROOT / "src").exists():
        ROOT.mkdir(parents=True, exist_ok=True)
        with tarfile.open(BUNDLE) as t:
            t.extractall(ROOT, filter="data")
else:
    ROOT = HERE.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))
for k, v in {
    "EMBED_MOCKS": "1",
    "DB_PATH": "/tmp/dm/app.db",
    "UPLOADS_DIR": "/tmp/dm/uploads",
    "PAGE_CACHE_DIR": "/tmp/dm/page_png",
    "LOG_PATH": "/tmp/dm/app.log.jsonl",
    "MANUALS_DIR": "/tmp/dm/no_manuals",
}.items():
    os.environ.setdefault(k, v)
Path("/tmp/dm").mkdir(parents=True, exist_ok=True)

from digital_mechanic.web.app import app  # noqa: E402,F401
