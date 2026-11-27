import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wrapper_versions_and_shell_defaults():
    android = json.loads((ROOT / "apps/android/package.json").read_text())
    desktop = json.loads((ROOT / "apps/desktop/package.json").read_text())
    assert android["version"] == desktop["version"] == "1.1.0"
    activity = (ROOT / "apps/android/native/MainActivity.java").read_text()
    electron = (ROOT / "apps/desktop/main.js").read_text()
    assert "digital-mechanic-iikuznitsa.vercel.app/m" in activity
    assert "digital-mechanic-iikuznitsa.vercel.app/desktop" in electron
    gradle = (ROOT / "apps/android/android/app/build.gradle").read_text()
    assert "versionCode 2" in gradle and 'versionName "1.1.0"' in gradle
