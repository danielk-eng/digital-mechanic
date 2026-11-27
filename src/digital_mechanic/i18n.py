"""Supported languages. UI strings live in web/static/i18n.json; answers are produced by the LLM
in the user's language while manual quotes stay verbatim (Russian) with document and page."""

LANGS = {
    "ru": {"name": "Русский", "llm": "русском", "stt": "ru"},
    "uz": {"name": "Oʻzbekcha", "llm": "узбекском (латиница)", "stt": "uz"},
    "tg": {"name": "Тоҷикӣ", "llm": "таджикском (кириллица)", "stt": "tg"},
    "kk": {"name": "Қазақша", "llm": "казахском (кириллица)", "stt": "kk"},
    "ky": {"name": "Кыргызча", "llm": "киргизском (кириллица)", "stt": "ky"},
}


def lang_or_default(code: str | None) -> str:
    return code if code in LANGS else "ru"


def answer_lang(user: dict | None) -> str:
    """Language of the agent's answers: its own setting, or the interface language when not set."""
    user = user or {}
    return user.get("answer_lang") if user.get("answer_lang") in LANGS else lang_or_default(user.get("lang"))
