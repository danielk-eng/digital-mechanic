"""Rebuild data/index/pages.jsonl.gz from the PDFs in MANUALS_DIR."""
import sys, time
from digital_mechanic.config import get_settings
from digital_mechanic.ingest.pages import build_index

s = get_settings()
t = time.time()
n = build_index(s.manuals_dir, s.engine_docs_path, s.index_path)
print(f"{n} pages -> {s.index_path} in {time.time()-t:.1f}s", file=sys.stderr)
