"""Task materials (organizers' stubs and manuals) are NOT in git (organizers 27.11): the stand downloads them at deploy time.

    TASK_MATERIALS_URL=<url> python scripts/fetch_task_materials.py        # stubs + vehicle cards
    TASK_MATERIALS_URL=<url> python scripts/fetch_manuals.py               # engine manual PDFs (same folder) + page index

TASK_MATERIALS_URL — where the organizers published the materials: a Yandex Disk public folder link (https://disk.yandex.ru/d/...)
or a direct URL of the stub archive (neurodvs_hackathon_mock_*.zip). The URL lives only in the environment (.env, Vercel/stand
settings), not in code.

Result (all git-ignored):
  vendor/neurodvs_mock/   the official ML / TOiR stubs (code + data/), our Dockerfile stays
  data/official/          vehicles.json, fault_types.json (+ our data/vehicle_overlay.json applied)
"""
import argparse, io, json, os, shutil, sys, urllib.parse, zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
YA = "https://cloud-api.yandex.net/v1/disk/public/resources"


def _get(url: str) -> bytes:
    r = httpx.get(url, timeout=600, follow_redirects=True)
    r.raise_for_status()
    return r.content


def _yandex_items(key: str) -> list[dict]:
    r = httpx.get(YA, params={"public_key": key, "limit": 200}, timeout=60)
    r.raise_for_status()
    return r.json().get("_embedded", {}).get("items", [])


def _yandex_file(key: str, path: str) -> bytes:
    r = httpx.get(YA + "/download", params={"public_key": key, "path": path}, timeout=60)
    r.raise_for_status()
    return _get(r.json()["href"])


def _stub_zip(url: str) -> tuple[bytes, list[dict]]:
    if "disk.yandex" in url or "yadi.sk" in url:
        items = _yandex_items(url)
        z = next((i for i in items if i["name"].startswith("neurodvs_hackathon_mock") and i["name"].endswith(".zip")), None)
        if not z:
            sys.exit("no neurodvs_hackathon_mock_*.zip in the folder")
        return _yandex_file(url, z["path"]), items
    return _get(url), []


def apply_overlay(cards: dict) -> int:
    ov = json.loads((ROOT / "data/vehicle_overlay.json").read_text(encoding="utf-8"))
    n = 0
    for v in cards.get("vehicles", []):
        for rule in ov.get("fill_model", []):
            if v.get("model") is None and v.get("make") == rule["make"] and v.get("engine_model") == rule["engine_model"]:
                v["model"] = rule["model"]; n += 1
    return n


def install_stubs(data: bytes) -> None:
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in zf.namelist() if not n.endswith("/")]
    root = os.path.commonprefix([n.split("/")[0] + "/" for n in names]) if all("/" in n for n in names) else ""
    dst = ROOT / "vendor/neurodvs_mock"
    dst.mkdir(parents=True, exist_ok=True)
    for n in names:
        rel = n[len(root):]
        if not rel or rel == "Dockerfile":  # our Dockerfile is in git
            continue
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(zf.read(n))
    off = ROOT / "data/official"
    off.mkdir(parents=True, exist_ok=True)
    cards = json.loads((dst / "data/vehicles.json").read_text(encoding="utf-8"))
    n = apply_overlay(cards)
    text = json.dumps(cards, ensure_ascii=False, indent=1) + "\n"
    (dst / "data/vehicles.json").write_text(text, encoding="utf-8")
    (off / "vehicles.json").write_text(text, encoding="utf-8")
    shutil.copyfile(dst / "data/fault_types.json", off / "fault_types.json")
    print(f"stubs -> vendor/neurodvs_mock, cards -> data/official ({len(cards.get('vehicles', []))} vehicles, overlay {n})")


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    url = os.environ.get("TASK_MATERIALS_URL", "").strip()
    if not url:
        sys.exit("set TASK_MATERIALS_URL (organizers' materials link) in the environment / .env")
    data, _ = _stub_zip(url)
    install_stubs(data)


if __name__ == "__main__":
    main()
