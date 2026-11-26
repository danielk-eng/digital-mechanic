"""Download the manuals listed in data/manuals_manifest.json from their original sources and build the indexes locally.

The public repository holds only code and the manifest (doc id, source URL, sha256, license note). Manufacturer manuals are
not redistributed: every stand fetches them itself.

    python scripts/fetch_manuals.py                    # everything: engine PDFs + vehicle manuals, both indexes
    python scripts/fetch_manuals.py --only EXTRA-MAZ555100-RE,EXTRA-PAZ32053-RE   # these docs only (others kept)
    python scripts/fetch_manuals.py --no-images --ocr never   # text only, no OCR of scanned pages

Engine manuals -> data/manuals/*.pdf -> data/index/pages.jsonl.gz (ingest/pages.py, page images rendered on demand).
Vehicle manuals -> data/extra/pdf/*.pdf -> data/index/extra_pages.jsonl.gz + data/extra/images/<slug>/page-NNN.jpg.
Scanned pages (no text layer) are OCR-ed with tesseract (rus) when it is installed. sha256 is checked; a changed file
stops the run unless --accept-changed (a new edition: re-check the license and update the manifest).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "manuals_manifest.json"
YA_API = "https://cloud-api.yandex.net/v1/disk/public/resources"
UA = {"User-Agent": "Mozilla/5.0 (digital-mechanic fetch_manuals)"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _stream(url: str, dst: Path) -> None:
    tmp = dst.with_suffix(".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=900, headers=UA) as r, open(tmp, "wb") as f:
        r.raise_for_status()
        for chunk in r.iter_bytes(1 << 20):
            f.write(chunk)
    tmp.replace(dst)


def _yandex_href(y: dict) -> str:
    items = httpx.get(YA_API, params={"public_key": y["public_key"], "limit": 200}, timeout=30).json()["_embedded"]["items"]
    it = next(i for i in items if i["name"] == y["name"])
    return httpx.get(YA_API + "/download", params={"public_key": y["public_key"], "path": it["path"]}, timeout=30).json()["href"]


def fetch(d: dict, dst: Path, accept_changed: bool) -> None:
    if dst.exists() and sha256(dst) == d["sha256"]:
        return
    errors = []
    for src in ([d["url"]] if d.get("url") else []) + (["yandex"] if d.get("yandex") else []):
        try:
            print(f"download {d['id']} <- {src if src != 'yandex' else 'Yandex Disk: ' + d['yandex']['name']}", file=sys.stderr)
            _stream(_yandex_href(d["yandex"]) if src == "yandex" else src, dst)
            break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src}: {type(e).__name__} {e}")
    else:
        raise SystemExit(f"{d['id']}: download failed: {errors}")
    got = sha256(dst)
    if got != d["sha256"]:
        msg = f"{d['id']}: sha256 {got} != manifest {d['sha256']} (new edition? re-check the license, then update the manifest)"
        if not accept_changed:
            raise SystemExit(msg)
        print("WARNING " + msg, file=sys.stderr)


def _fix_cp1251(t: str) -> str:
    """Text layers written in cp1251 but declared latin-1 come out as «Ðóêîâîäñòâî»: re-decode."""
    hi = sum(1 for ch in t if "\u00c0" <= ch <= "\u00ff")
    if hi < 20 or hi < 0.3 * sum(ch.isalpha() for ch in t):
        return t
    try:
        return t.encode("latin-1", "ignore").decode("cp1251", "ignore")
    except Exception:  # noqa: BLE001
        return t


def _cyr(t: str) -> int:
    return sum(1 for ch in t.lower() if "а" <= ch <= "я")


def page_text(pdf: Path, page: int, ocr: bool, cp1251_fix: bool, drop: str = "") -> str:
    t = subprocess.run(["pdftotext", "-q", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    if cp1251_fix:
        t = _fix_cp1251(t)
    if ocr and _cyr(t) < 80 and shutil.which("tesseract"):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["pdftoppm", "-q", "-f", str(page), "-l", str(page), "-r", "250", "-gray", "-png", str(pdf), f"{td}/p"], check=True)
            png = next(Path(td).glob("p*.png"))
            o = subprocess.run(["tesseract", str(png), "-", "-l", "rus+eng", "--psm", "3"], capture_output=True, text=True,
                               env={**os.environ, "OMP_THREAD_LIMIT": "1"}).stdout
            if _cyr(o) > _cyr(t):
                t = o
    lines = [l.rstrip() for l in t.replace("\f", "").splitlines() if not (drop and re.search(drop, l))]  # scanner watermarks
    return "\n".join(l for l in lines if l.strip()).strip()


def render_images(pdf: Path, out_dir: Path, pages: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if len(list(out_dir.glob("page-*.jpg"))) >= pages:
        return
    subprocess.run(["pdftoppm", "-q", "-r", "80", "-jpeg", "-jpegopt", "quality=70", str(pdf), str(out_dir / "page")], check=True)
    for p in out_dir.glob("page-*.jpg"):  # pdftoppm pads to the page count width: normalise to page-001.jpg
        n = int(p.stem.split("-")[-1])
        p.rename(out_dir / f"page-{n:03d}.jpg")


def build_vehicle(d: dict, pdf: Path, images: bool, ocr: bool, workers: int) -> list[dict]:
    n = int(subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout.split("Pages:")[1].split()[0])
    with ThreadPoolExecutor(workers) as ex:
        texts = list(ex.map(lambda p: page_text(pdf, p, ocr and d.get("ocr", True), d.get("cp1251_fix", False), d.get("drop_lines", "")), range(1, n + 1)))
    img_dir = ROOT / "data" / "extra" / "images" / d["slug"]
    if images:
        render_images(pdf, img_dir, n)
    rows = []
    for p, t in enumerate(texts, 1):
        img = f"images/{d['slug']}/page-{p:03d}.jpg"
        rows.append({"doc_id": d["id"], "document": d["document"], "make": d["make"], "models": d["models"], "source": d["source"],
                     "page": p, "text": t, "image": img if (ROOT / "data" / "extra" / img).exists() else None})
    print(f"{d['id']}: {n} pages, {sum(1 for t in texts if _cyr(t) >= 80)} with text", file=sys.stderr)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated doc ids")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--ocr", choices=("auto", "never"), default="auto")
    ap.add_argument("--accept-changed", action="store_true")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()
    docs = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    sel = [d for d in docs if not only or d["id"] in only]
    if only - {d["id"] for d in docs}:
        raise SystemExit(f"unknown ids: {sorted(only - {d['id'] for d in docs})}")
    for d in sel:
        dst = ROOT / "data" / ("manuals" if d["kind"] == "engine" else "extra/pdf") / d["file"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        fetch(d, dst, a.accept_changed)
    if a.download_only:
        return
    if any(d["kind"] == "engine" for d in sel):
        sys.path.insert(0, str(ROOT / "src"))
        from digital_mechanic.config import get_settings
        from digital_mechanic.ingest.pages import build_index
        s = get_settings()
        print(f"engine index: {build_index(s.manuals_dir, s.engine_docs_path, s.index_path)} pages", file=sys.stderr)
    veh = [d for d in sel if d["kind"] == "vehicle"]
    if veh:
        path = ROOT / "data" / "index" / "extra_pages.jsonl.gz"
        ids = {d["id"] for d in veh}
        keep = []
        if path.exists() and only:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                keep = [json.loads(l) for l in f if json.loads(l)["doc_id"] not in ids]
        rows = keep + [r for d in veh for r in build_vehicle(d, ROOT / "data" / "extra" / "pdf" / d["file"], not a.no_images, a.ocr == "auto", a.workers)]
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"extra index: {len(rows)} pages -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
