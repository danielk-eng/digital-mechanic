#!/usr/bin/env python3
"""Generate launcher and splash resources from apps/android/resources/icon.png."""
from __future__ import annotations

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = Image.open(ROOT / "android" / "resources" / "icon.png").convert("RGBA")
SPLASH_ICON = Image.open(ROOT / "android" / "resources" / "splash-icon.png").convert("RGBA")
RES = ROOT / "android" / "android" / "app" / "src" / "main" / "res"
BLUE = "#4C8DFF"
DARK = "#0E1013"

DENSITIES = {
    "mdpi": 1,
    "hdpi": 1.5,
    "xhdpi": 2,
    "xxhdpi": 3,
    "xxxhdpi": 4,
}


def fit(im: Image.Image, size: int) -> Image.Image:
    return im.resize((size, size), Image.Resampling.LANCZOS)


def save(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG")
    print("wrote", path)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    print("wrote", path)


def main() -> None:
    if not (ROOT / "android" / "android").exists():
        raise SystemExit("android/android missing")
    for name, scale in DENSITIES.items():
        folder = RES / f"mipmap-{name}"
        save(fit(SRC, int(48 * scale)), folder / "ic_launcher.png")
        save(fit(SRC, int(48 * scale)), folder / "ic_launcher_round.png")
        # adaptive foreground: 108dp canvas, logo in the inner 72dp
        canvas = int(108 * scale)
        inner = int(72 * scale)
        fg = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        logo = fit(SRC, inner)
        xy = (canvas - inner) // 2
        fg.paste(logo, (xy, xy), logo)
        save(fg, RES / f"drawable-{name}" / "ic_launcher_foreground.png")
        # splash centered icon ~192dp on xxx but store per density 160dp
        save(fit(SPLASH_ICON, int(160 * scale)), RES / f"drawable-{name}" / "splash_icon.png")

    write(RES / "drawable" / "ic_launcher_background.xml", f"""<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <solid android:color="{BLUE}" />
</shape>
""")
    write(RES / "mipmap-anydpi-v26" / "ic_launcher.xml", """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
""")
    write(RES / "mipmap-anydpi-v26" / "ic_launcher_round.xml", """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
""")
        # Capacitor ships drawable/splash.png; the layer-list must replace it.
    for png in RES.rglob("splash.png"):
        png.unlink()
        print("removed", png)
    
    write(RES / "drawable" / "splash.xml", """<?xml version="1.0" encoding="utf-8"?>
<layer-list xmlns:android="http://schemas.android.com/apk/res/android">
    <item android:drawable="@color/splash_background" />
    <item>
        <bitmap android:gravity="center" android:src="@drawable/splash_icon" />
    </item>
</layer-list>
""")
    write(RES / "values" / "colors.xml", f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="colorPrimary">#4C8DFF</color>
    <color name="colorPrimaryDark">{DARK}</color>
    <color name="colorAccent">#4C8DFF</color>
    <color name="splash_background">{DARK}</color>
</resources>
""")
    styles = RES / "values" / "styles.xml"
    if styles.exists():
        text = styles.read_text(encoding="utf-8")
        if "splash" not in text.lower() or "AppTheme.NoActionBarLaunch" in text:
            # Capacitor generates AppTheme.NoActionBarLaunch with splash
            if "android:windowBackground" not in text:
                text = text.replace(
                    'name="AppTheme.NoActionBarLaunch"',
                    'name="AppTheme.NoActionBarLaunch" parent="AppTheme.NoActionBar"',
                )
            text = re_sub_window(text)
            write(styles, text)
    else:
        write(styles, f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <style name="AppTheme" parent="Theme.AppCompat.DayNight.NoActionBar">
        <item name="colorPrimary">@color/colorPrimary</item>
        <item name="colorPrimaryDark">@color/colorPrimaryDark</item>
        <item name="colorAccent">@color/colorAccent</item>
        <item name="android:statusBarColor">@color/splash_background</item>
        <item name="android:navigationBarColor">@color/splash_background</item>
    </style>
    <style name="AppTheme.NoActionBar" parent="AppTheme">
        <item name="windowActionBar">false</item>
        <item name="windowNoTitle">true</item>
        <item name="android:background">@null</item>
    </style>
    <style name="AppTheme.NoActionBarLaunch" parent="AppTheme.NoActionBar">
        <item name="android:windowBackground">@drawable/splash</item>
    </style>
</resources>
""")
    print("make-android-res: ok")


def re_sub_window(text: str) -> str:
    import re
    if "@drawable/splash" in text:
        return text
    # inject windowBackground into launch theme
    def repl(m):
        body = m.group(2)
        if "windowBackground" in body:
            body = re.sub(r'<item name="android:windowBackground">[^<]+</item>',
                          '<item name="android:windowBackground">@drawable/splash</item>', body)
        else:
            body = body.replace("</style>", '        <item name="android:windowBackground">@drawable/splash</item>\n    </style>')
        return m.group(1) + body
    text2, n = re.subn(
        r'(<style name="AppTheme\.NoActionBarLaunch"[^>]*>)(.*?)</style>',
        lambda m: m.group(1) + (m.group(2) if "windowBackground" in m.group(2) else m.group(2) + '        <item name="android:windowBackground">@drawable/splash</item>\n    ') + "</style>",
        text,
        count=1,
        flags=re.S,
    )
    if n:
        return text2
    return text + """
    <style name="AppTheme.NoActionBarLaunch" parent="AppTheme.NoActionBar">
        <item name="android:windowBackground">@drawable/splash</item>
    </style>
"""


if __name__ == "__main__":
    main()
