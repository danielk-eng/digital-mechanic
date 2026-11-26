"""Download the six official PDFs from the customer's public Yandex Disk folder into data/manuals/.

The app answers without PDFs (text index is in the repo); PDFs are only needed to show page images.
Usage: python scripts/fetch_manuals.py [public_url]
"""
import io
import sys
import zipfile
from pathlib import Path

import httpx

URL = sys.argv[1] if len(sys.argv) > 1 else "${TASK_MATERIALS_URL}"
MAP = {
    "ЯМЗ_236_руководство по эксплуатации.pdf": "yamz_236_re.pdf",
    "ЯМЗ_236_руководство по РЕМОНТУ.pdf": "yamz_236_rk.pdf",
    "yamz-536_re_2016.pdf": "yamz_536_re.pdf",
    "yamz-536_rk_2013.pdf": "yamz_536_rk.pdf",
    "zmz-5234_rk_2025.pdf": "zmz_5234_rk.pdf",
    "zmz-40904-40905_rk_2025.pdf": "zmz_409_rk.pdf",
}
out = Path(__file__).resolve().parents[1] / "data" / "manuals"
out.mkdir(parents=True, exist_ok=True)
api = "https://cloud-api.yandex.net/v1/disk/public/resources"
items = httpx.get(api, params={"public_key": URL, "limit": 100}, timeout=30).json()["_embedded"]["items"]
for it in items:
    dst = MAP.get(it["name"])
    if not dst or (out / dst).exists():
        continue
    href = httpx.get(api + "/download", params={"public_key": URL, "path": it["path"]}, timeout=30).json()["href"]
    print("download", it["name"], "->", dst)
    with httpx.stream("GET", href, follow_redirects=True, timeout=600) as r, open(out / dst, "wb") as f:
        for chunk in r.iter_bytes(1 << 20):
            f.write(chunk)
print("done:", sorted(p.name for p in out.glob("*.pdf")))
