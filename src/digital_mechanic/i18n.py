"""Supported languages. UI strings live in web/static/i18n.json; answers are produced by the LLM
in the user's language while manual quotes stay verbatim (Russian) with document and page."""

LANGS = {
    "ru": {"name": "Русский", "llm": "русском", "stt": "ru"},
    "uz": {"name": "Oʻzbekcha", "llm": "узбекском (латиница)", "stt": "uz"},
    "tg": {"name": "Тоҷикӣ", "llm": "таджикском (кириллица)", "stt": "tg"},
    "kk": {"name": "Қазақша", "llm": "казахском (кириллица)", "stt": "kk"},
    "hi": {"name": "हिन्दी", "llm": "хинди (деванагари)", "stt": "hi"},
    "vi": {"name": "Tiếng Việt", "llm": "вьетнамском", "stt": "vi"},
}


def lang_or_default(code: str | None) -> str:
    return code if code in LANGS else "ru"
