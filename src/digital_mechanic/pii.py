"""PII masking before anything leaves the server (LLM, STT) and before logging (152-FZ)."""

from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<!\w)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"), "[телефон]"),
    (re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[\s-]?\d{2}(?!\d)"), "[СНИЛС]"),
    (re.compile(r"(?i)паспорт\w*[\s:№#]*\d{2}\s?\d{2}\s?\d{6}"), "паспорт [скрыто]"),
    (re.compile(r"(?<!\d)\d{4}\s\d{6}(?!\d)"), "[документ]"),
    (re.compile(r"(?i)(?:ИНН|inn)[\s:№]*\d{10,12}"), "ИНН [скрыто]"),
    (re.compile(r"(?<!\d)(?:\d{4}[\s-]?){3}\d{4}(?!\d)"), "[карта]"),
    # Surname + initials: "Иванов И.И.", "И.И. Иванов"
    (re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.(?!\w)"), "[ФИО]"),
    (re.compile(r"\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ][а-яё]{2,}\b"), "[ФИО]"),
]


def mask(text: str | None) -> str:
    if not text:
        return text or ""
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text)
    return text
