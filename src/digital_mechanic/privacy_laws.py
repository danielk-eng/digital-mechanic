"""Personal-data law per country (checked 26.11.2026, sources in docs/152-ФЗ.md, section «Персональные данные по странам»).
`localize`: the law requires personal data of citizens to be stored in the country -> speech-to-text runs local-first
(audio stays on the stand; voice can be biometric data)."""
from __future__ import annotations

COUNTRIES: dict[str, dict] = {
    "RU": {"flag": "RU", "name": {"ru": "Россия", "uz": "Rossiya", "tg": "Россия", "kk": "Ресей", "hi": "रूस", "vi": "Nga", "bn": "রাশিয়া", "ky": "Россия"},
           "law": "Федеральный закон от 27.07.2006 № 152-ФЗ «О персональных данных»", "short": "152-ФЗ", "localize": True,
           "localize_note": "ч. 5 ст. 18: запись, хранение и обработка ПДн граждан РФ — в базах данных на территории РФ"},
    "UZ": {"flag": "UZ", "name": {"ru": "Узбекистан", "uz": "Oʻzbekiston", "tg": "Ӯзбекистон", "kk": "Өзбекстан", "hi": "उज़्बेकिस्तान", "vi": "Uzbekistan", "bn": "উজবেকিস্তান", "ky": "Өзбекстан"},
           "law": "Закон Республики Узбекистан от 02.07.2019 № ЗРУ-547 «О персональных данных»", "short": "ЗРУ-547", "localize": True,
           "localize_note": "ст. 27¹ (ред. ЗРУ-1125 от 26.03.2026): обязательно в РУз хранятся биометрические и генетические данные; "
                            "голос — потенциально биометрия, поэтому распознавание речи локальное"},
    "KZ": {"flag": "KZ", "name": {"ru": "Казахстан", "uz": "Qozogʻiston", "tg": "Қазоқистон", "kk": "Қазақстан", "hi": "कज़ाख़स्तान", "vi": "Kazakhstan", "bn": "কাজাখস্তান", "ky": "Казакстан"},
           "law": "Закон Республики Казахстан от 21.05.2013 № 94-V «О персональных данных и их защите»", "short": "№ 94-V", "localize": True,
           "localize_note": "п. 2 ст. 12: хранение ПДн — в базе и (или) цифровом объекте на территории РК"},
    "TJ": {"flag": "TJ", "name": {"ru": "Таджикистан", "uz": "Tojikiston", "tg": "Тоҷикистон", "kk": "Тәжікстан", "hi": "ताजिकिस्तान", "vi": "Tajikistan", "bn": "তাজিকিস্তান", "ky": "Тажикстан"},
           "law": "Закон Республики Таджикистан от 03.08.2018 № 1537 «О защите персональных данных»", "short": "№ 1537", "localize": False,
           "localize_note": "прямого требования локализации нет; трансграничная передача — по правилам закона"},
    "IN": {"flag": "IN", "name": {"ru": "Индия", "uz": "Hindiston", "tg": "Ҳиндустон", "kk": "Үндістан", "hi": "भारत", "vi": "Ấn Độ", "bn": "ভারত", "ky": "Индия"},
           "law": "Digital Personal Data Protection Act, 2023 (No. 22 of 2023); DPDP Rules, 2025", "short": "DPDP Act 2023", "localize": False,
           "localize_note": "общей локализации нет; основные обязанности по Rules 2025 — с 13.05.2027"},
    "VN": {"flag": "VN", "name": {"ru": "Вьетнам", "uz": "Vyetnam", "tg": "Ветнам", "kk": "Вьетнам", "hi": "वियतनाम", "vi": "Việt Nam", "bn": "ভিয়েতনাম", "ky": "Вьетнам"},
           "law": "Luật Bảo vệ dữ liệu cá nhân số 91/2025/QH15; Nghị định 356/2025/NĐ-CP", "short": "Luật 91/2025/QH15", "localize": False,
           "localize_note": "с 01.01.2026 действуют Закон 91/2025/QH15 и Постановление 356/2025/NĐ-CP; Постановление 13/2023/NĐ-CP утратило силу. "
                            "Трансграничная передача — с досье оценки воздействия (форма 09)"},
    "KG": {"flag": "KG", "name": {"ru": "Кыргызстан", "uz": "Qirgʻiziston", "tg": "Қирғизистон", "kk": "Қырғызстан", "hi": "किर्गिज़स्तान", "vi": "Kyrgyzstan",
                                  "bn": "কিরগিজস্তান", "ky": "Кыргызстан"},
           "law": "Цифровой кодекс Кыргызской Республики от 31.07.2025 № 178 (персональные данные; в силу с 05.02.2026)", "short": "Цифровой кодекс КР № 178",
           "localize": False, "stt_local": True,
           "localize_note": "Закон КР от 14.04.2008 № 58 «Об информации персонального характера» утратил силу 05.02.2026. Общей локализации нет (ст. 31), "
                            "но перечень стран с адекватной защитой не опубликован: передача за рубеж — только по согласию или договору (ст. 89). "
                            "Поэтому речь по умолчанию распознаётся на стенде"},
    "BY": {"flag": "BY", "name": {"ru": "Беларусь", "uz": "Belarus", "tg": "Беларус", "kk": "Беларусь", "hi": "बेलारूस", "vi": "Belarus",
                                  "bn": "বেলারুশ", "ky": "Беларусь"},
           "law": "Закон Республики Беларусь от 07.05.2021 № 99-З «О защите персональных данных» (ред. Закона от 16.03.2026 № 134-З)", "short": "№ 99-З",
           "localize": False, "stt_local": True,
           "localize_note": "Требования хранить данные в стране нет; передача — только в страны с надлежащей защитой (Конвенция 108 и ЕАЭС, приказ НЦЗПД № 14), "
                            "в другие — по согласию с предупреждением о рисках (ст. 9). Облачный сервис речи вне этого перечня, поэтому речь по умолчанию на стенде"},
}
DEFAULT = "RU"


def country(code: str | None) -> str:
    c = (code or "").upper()
    return c if c in COUNTRIES else DEFAULT


def requires_localization(code: str | None) -> bool:
    """Local-first speech recognition by default: data localization law (RU, UZ, KZ) or cross-border limits (KG, BY)."""
    c = COUNTRIES[country(code)]
    return c.get("stt_local", c["localize"])


def public_list(lang: str = "ru") -> list[dict]:
    return [{"code": k, "name": v["name"].get(lang) or v["name"]["ru"], "law": v["law"], "short": v["short"], "localize": v.get("stt_local", v["localize"]),
             "localize_note": v["localize_note"]} for k, v in COUNTRIES.items()]
