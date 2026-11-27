"""Every UI string of the Russian section exists in each interface language (no silent fallback to Russian),
and demo account titles on the login screen have a per-language string."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N = json.loads((ROOT / "src/digital_mechanic/web/static/i18n.json").read_text(encoding="utf-8"))


def test_all_languages_have_every_key():
    ru = I18N["ru"]
    for lang, m in I18N.items():
        missing = [k for k in ru if not m.get(k)]
        assert not missing, f"{lang}: {missing}"


def test_demo_titles_translated():
    accs = json.loads((ROOT / "data/demo_users.json").read_text(encoding="utf-8"))["accounts"]
    for a in accs:
        assert I18N["ru"]["title_" + a["key"]] == a["title"]
        for lang in ("uz", "hi", "vi", "bn"):
            assert not any("а" <= c <= "я" for c in I18N[lang]["title_" + a["key"]].lower()), (lang, a["key"])


def test_login_titles_use_lang_string():
    js = (ROOT / "src/digital_mechanic/web/static/app.js").read_text(encoding="utf-8")
    assert 'TT("title_" + a.key, a.title)' in js
    assert "applyI18n(); demoAccounts(); }" in js  # re-rendered when the language changes on the login screen


def test_language_picker_offers_every_language():
    import re
    from digital_mechanic.i18n import LANGS
    js = (ROOT / "src/digital_mechanic/web/static/app.js").read_text(encoding="utf-8")
    names = dict(re.findall(r'(\w+): "([^"]+)"', re.search(r"const LANG_NAMES = \{([^}]*)\}", js).group(1)))
    assert names == {k: v["name"] for k, v in LANGS.items()}  # bn/ky/be were imported but not selectable
    assert set(I18N) == set(LANGS)
