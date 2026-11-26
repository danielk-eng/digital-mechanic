#!/usr/bin/env python3
"""Idempotent patches for the Capacitor Android project."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android" / "android"
NATIVE = ROOT / "android" / "native"
PKG = ANDROID / "app" / "src" / "main" / "java" / "ru" / "iikuznitsa" / "mechanic"
RES = ANDROID / "app" / "src" / "main" / "res"
MAIN = ANDROID / "app" / "src" / "main"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    if p.exists() and p.read_text(encoding="utf-8") == text:
        return
    p.write_text(text, encoding="utf-8")
    print("wrote", p)


def patch_manifest() -> None:
    path = MAIN / "AndroidManifest.xml"
    text = read(path)
    perms = """    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" android:maxSdkVersion="32" />
    <uses-permission android:name="android.permission.READ_MEDIA_IMAGES" />
    <uses-permission android:name="android.permission.READ_MEDIA_VIDEO" />
    <uses-feature android:name="android.hardware.microphone" android:required="false" />
    <uses-feature android:name="android.hardware.camera" android:required="false" />
    <uses-feature android:name="android.hardware.camera.autofocus" android:required="false" />
"""
    # strip existing copies of these permissions to avoid duplicates, then insert
    drop = [
        r'\s*<uses-permission android:name="android.permission.(INTERNET|ACCESS_NETWORK_STATE|RECORD_AUDIO|MODIFY_AUDIO_SETTINGS|CAMERA|READ_EXTERNAL_STORAGE|READ_MEDIA_IMAGES|READ_MEDIA_VIDEO)"[^/]*/>',
        r'\s*<uses-feature android:name="android.hardware.(microphone|camera|camera.autofocus)"[^/]*/>',
    ]
    for pat in drop:
        text = re.sub(pat, "", text)
    if "<uses-permission" in text:
        text = re.sub(r"(<manifest[^>]*>)", r"\1\n" + perms, text, count=1)
    else:
        text = text.replace("</manifest>", perms + "</manifest>")

    # application attributes
    if "usesCleartextTraffic" not in text:
        text = text.replace(
            "<application",
            '<application android:usesCleartextTraffic="true"\n        android:networkSecurityConfig="@xml/network_security_config"\n        android:allowBackup="false"',
            1,
        )
    else:
        if "networkSecurityConfig" not in text:
            text = text.replace(
                "<application",
                '<application android:networkSecurityConfig="@xml/network_security_config"',
                1,
            )
        text = text.replace('android:allowBackup="true"', 'android:allowBackup="false"')
        if 'android:allowBackup="false"' not in text:
            text = text.replace("<application", '<application android:allowBackup="false"', 1)

    # activity launchMode / windowSoftInputMode / shortcuts / labels
    text = re.sub(
        r'<activity([^>]*android:name="[^"]*MainActivity"[^>]*)>',
        _activity_tag,
        text,
        count=1,
        flags=re.S,
    )
    if "android.app.shortcuts" not in text:
        if "</intent-filter>" in text:
            text = text.replace(
                "</intent-filter>",
                """</intent-filter>

            <meta-data
                android:name="android.app.shortcuts"
                android:resource="@xml/shortcuts" />
""",
                1,
            )
    # never leave the invalid activity attribute
    text = text.replace('\n            android:shortcuts="@xml/shortcuts"', "")
    text = re.sub(r'\s+android:shortcuts="@xml/shortcuts"', "", text)
    write(path, text)


def _activity_tag(m: re.Match) -> str:
    attrs = m.group(1)
    def set_attr(src: str, name: str, value: str) -> str:
        if f"android:{name}=" in src:
            return re.sub(rf'android:{name}="[^"]*"', f'android:{name}="{value}"', src)
        return src + f'\n            android:{name}="{value}"'

    attrs = set_attr(attrs, "launchMode", "singleTask")
    attrs = set_attr(attrs, "windowSoftInputMode", "adjustResize")
    attrs = set_attr(attrs, "label", "@string/app_name")
    attrs = set_attr(attrs, "exported", "true")
    return f"<activity{attrs}>"


def patch_strings() -> None:
    path = RES / "values" / "strings.xml"
    text = read(path) if path.exists() else '<resources>\n</resources>\n'
    def upsert(text: str, name: str, value: str) -> str:
        pat = rf'<string name="{name}">[^<]*</string>'
        el = f'<string name="{name}">{value}</string>'
        if re.search(pat, text):
            return re.sub(pat, el, text)
        return text.replace("</resources>", f"    {el}\n</resources>")
    text = upsert(text, "app_name", "Цифровой механик")
    text = upsert(text, "title_activity_main", "Цифровой механик")
    text = upsert(text, "package_name", "ru.iikuznitsa.mechanic")
    text = upsert(text, "custom_url_scheme", "ru.iikuznitsa.mechanic")
    text = upsert(text, "shortcut_server_short", "Сервер")
    text = upsert(text, "shortcut_server_long", "Адрес сервера")
    write(path, text)


def copy_native() -> None:
    dest = PKG / "MainActivity.java"
    shutil.copy2(NATIVE / "MainActivity.java", dest)
    print("copied MainActivity.java")
    write(RES / "xml" / "network_security_config.xml", read(NATIVE / "network_security_config.xml"))
    write(RES / "xml" / "shortcuts.xml", read(NATIVE / "shortcuts.xml"))


def patch_gradle() -> None:
    path = ANDROID / "app" / "build.gradle"
    text = read(path)
    marker = "// MECHANIC_SIGNING"
    if marker not in text:
        text += """
// MECHANIC_SIGNING
def ksFile = System.getenv("MECHANIC_KEYSTORE")
def ksPassFile = System.getenv("MECHANIC_KEYSTORE_PASSFILE")
if (ksFile != null && file(ksFile).exists() && ksPassFile != null && file(ksPassFile).exists()) {
    def ksPass = file(ksPassFile).getText("UTF-8").trim()
    android.signingConfigs {
        releaseReady {
            storeFile file(ksFile)
            storePassword ksPass
            keyAlias "mechanic"
            keyPassword ksPass
        }
    }
    android.buildTypes.release.signingConfig = android.signingConfigs.releaseReady
    android.buildTypes.debug.signingConfig = android.signingConfigs.releaseReady
}
"""
        write(path, text)
    # local.properties
    sdk = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or "/workspace/android-sdk"
    write(ANDROID / "local.properties", f"sdk.dir={sdk}\n")


def main() -> None:
    if not ANDROID.exists():
        raise SystemExit(f"Capacitor Android project missing at {ANDROID}. Run: npx cap add android")
    copy_native()
    patch_manifest()
    patch_strings()
    patch_gradle()
    print("patch-android: ok")


if __name__ == "__main__":
    main()
