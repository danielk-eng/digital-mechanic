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
        for lang in ("uz",):  # uz is the remaining non-Cyrillic product language
            assert not any("а" <= c <= "я" for c in I18N[lang]["title_" + a["key"]].lower()), (lang, a["key"])


def test_login_titles_use_lang_string():
    js = (ROOT / "src/digital_mechanic/web/static/app.js").read_text(encoding="utf-8")
    assert 'TT("title_" + a.key, a.title)' in js
    assert "applyI18n(); demoAccounts(); }" in js  # re-rendered when the language changes on the login screen


def test_discontinued_languages_are_not_product_languages():
    from digital_mechanic.i18n import LANGS
    assert set(LANGS) == {"ru", "uz", "tg", "kk", "ky"}
    assert not {"hi", "vi", "bn", "be"} & set(LANGS)
    assert not {"hi", "vi", "bn", "be"} & set(I18N)


def test_language_picker_offers_every_language():
    import re
    from digital_mechanic.i18n import LANGS
    js = (ROOT / "src/digital_mechanic/web/static/app.js").read_text(encoding="utf-8")
    names = dict(re.findall(r'(\w+): "([^"]+)"', re.search(r"const LANG_NAMES = \{([^}]*)\}", js).group(1)))
    assert names == {k: v["name"] for k, v in LANGS.items()}
    assert set(I18N) == set(LANGS)


def test_chief_bar_qr_button_translated_and_icon_on_phone():
    html = (ROOT / "src/digital_mechanic/web/static/manager.html").read_text(encoding="utf-8")
    assert 'id="myqr" data-t-aria="qr_login"' in html and 'data-icon="qr-code"' in html and 'data-t="qr_login"' in html
    assert '"qr-code"' in (ROOT / "src/digital_mechanic/web/static/icons.js").read_text(encoding="utf-8")
    assert all(I18N[l].get("qr_login") for l in I18N)
