"""Personal-data law per country (checked 26.11.2026, sources in docs/152-ФЗ.md, section «Персональные данные по странам»).
`localize`: the law requires personal data of citizens to be stored in the country -> speech-to-text runs local-first
(audio stays on the stand; voice can be biometric data)."""
from __future__ import annotations

COUNTRIES: dict[str, dict] = {
    "RU": {"flag": "RU", "name": {"ru": "Россия", "uz": "Rossiya", "tg": "Россия", "kk": "Ресей", "ky": "Россия"},
           "law": "Федеральный закон от 27.07.2006 № 152-ФЗ «О персональных данных»", "short": "152-ФЗ", "localize": True,
           "localize_note": "ч. 5 ст. 18: запись, хранение и обработка ПДн граждан РФ — в базах данных на территории РФ"},
    "UZ": {"flag": "UZ", "name": {"ru": "Узбекистан", "uz": "Oʻzbekiston", "tg": "Ӯзбекистон", "kk": "Өзбекстан", "ky": "Өзбекстан"},
           "law": "Закон Республики Узбекистан от 02.07.2019 № ЗРУ-547 «О персональных данных»", "short": "ЗРУ-547", "localize": True,
           "localize_note": "ст. 27¹ (ред. ЗРУ-1125 от 26.03.2026): обязательно в РУз хранятся биометрические и генетические данные; "
                            "голос — потенциально биометрия, поэтому распознавание речи локальное"},
    "KZ": {"flag": "KZ", "name": {"ru": "Казахстан", "uz": "Qozogʻiston", "tg": "Қазоқистон", "kk": "Қазақстан", "ky": "Казакстан"},
           "law": "Закон Республики Казахстан от 21.05.2013 № 94-V «О персональных данных и их защите»", "short": "№ 94-V", "localize": True,
           "localize_note": "п. 2 ст. 12: хранение ПДн — в базе и (или) цифровом объекте на территории РК"},
    "TJ": {"flag": "TJ", "name": {"ru": "Таджикистан", "uz": "Tojikiston", "tg": "Тоҷикистон", "kk": "Тәжікстан", "ky": "Тажикстан"},
           "law": "Закон Республики Таджикистан от 03.08.2018 № 1537 «О защите персональных данных»", "short": "№ 1537", "localize": False,
           "localize_note": "прямого требования локализации нет; трансграничная передача — по правилам закона"},
    "KG": {"flag": "KG", "name": {"ru": "Кыргызстан", "uz": "Qirgʻiziston", "tg": "Қирғизистон", "kk": "Қырғызстан",
                                  "ky": "Кыргызстан"},
           "law": "Цифровой кодекс Кыргызской Республики от 31.07.2025 № 178 (персональные данные; в силу с 05.02.2026)", "short": "Цифровой кодекс КР № 178",
           "localize": False, "stt_local": True,
           "localize_note": "Закон КР от 14.04.2008 № 58 «Об информации персонального характера» утратил силу 05.02.2026. Общей локализации нет (ст. 31), "
                            "но перечень стран с адекватной защитой не опубликован: передача за рубеж — только по согласию или договору (ст. 89). "
                            "Поэтому речь по умолчанию распознаётся на стенде"},
}
DEFAULT = "RU"


def country(code: str | None) -> str:
    c = (code or "").upper()
    return c if c in COUNTRIES else DEFAULT


def requires_localization(code: str | None) -> bool:
    """Local-first speech recognition by default: data localization law (RU, UZ, KZ) or cross-border limits (KG)."""
    c = COUNTRIES[country(code)]
    return c.get("stt_local", c["localize"])


def public_list(lang: str = "ru") -> list[dict]:
    return [{"code": k, "name": v["name"].get(lang) or v["name"]["ru"], "law": v["law"], "short": v["short"], "localize": v.get("stt_local", v["localize"]),
             "localize_note": v["localize_note"]} for k, v in COUNTRIES.items()]
