"""Query expansion for symptom questions ("почему стучит двигатель") -> manual vocabulary + fault tables.

Mechanics describe what they hear/see; the manuals index faults in tables "внешнее проявление / причина /
способ устранения". A symptom word in the question adds (1) its manual synonyms and (2) a fault-table query;
pages that are fault tables get a boost. All reformulations are fused by reciprocal rank (bm25.search_multi).
"""

from __future__ import annotations

import re

# trigger (regex on lower-case question) -> manual terms
SYMPTOMS: list[tuple[str, str]] = [
    (r"стук|стуч|колот|цока|цокот", "стук стуки двигатель стучит посторонний стук"),
    (r"шум|гремит|грохот|гул|гуд", "посторонний шум стук повышенный шум"),
    (r"вибрац|вибрир|тряс|трус|дрож|колбас", "повышенная вибрация неравномерная работа двигателя"),
    (r"троит|троен|неровно|перебо", "неравномерная работа двигателя перебои в работе цилиндров"),
    (r"дым|копт", "дымление дымный выпуск цвет отработавших газов"),
    (r"перегр|греет|кипит|закип|температур", "перегрев температура охлаждающей жидкости выше нормы"),
    (r"не завод|не пуска|не запуска|плохо завод|тяжело завод|не крутит", "двигатель не пускается пуск двигателя"),
    (r"не тянет|мощност|слаб", "недостаточная мощность двигатель не развивает мощность"),
    (r"свист|визж|скрип", "свист ремня натяжение ремня пробуксовка ремня"),
    (r"давлени\w* масл|масло дав|лампа масл", "пониженное давление масла в системе смазки"),
    (r"расход масл|ест масл|жр[её]т масл|масложор|угар", "повышенный расход масла на угар"),
    (r"теч|подтек|потек|сопл|капает", "подтекание течь негерметичность уплотнений"),
    (r"глохн|глохнет|останавлива", "двигатель внезапно останавливается неустойчивая работа на холостом ходу"),
    (r"не заряж|нет заряд|заряд\w* нет|сел\w* аккумулятор|аккумулятор\w* сад|генератор\w* не ", "генератор не заряжает аккумуляторная батарея"),
]

# maintenance / procedure questions ("как проверить натяжение ремня генератора") are not symptoms: they need the
# adjustment section, not the fault tables. trigger -> manual section vocabulary
MAINT: list[tuple[str, str]] = [
    (r"натяж\w*.{0,30}ремн|ремн\w*.{0,30}натяж|прогиб\w* ремн", "регулировка натяжения ремней прогиб ремня усилие"),
    (r"зазор\w* клапан|клапан\w*.{0,20}зазор", "регулировка тепловых зазоров клапанов щуп коромысло"),
    (r"момент\w* затяж|затяжк\w*", "момент затяжки болтов гаек последовательность затяжки"),
    (r"угол опереж|опережени\w* впрыск", "установка угла опережения впрыскивания топлива"),
]
MAINT_RX = re.compile(r"(?i)как (?:проверить|отрегулировать|регулировать|выставить|снять|установить|заменить|натянуть)|регулировк|натяжени|момент затяжк|зазор")

FAULT_RX = re.compile(r"(?i)способ\w* устранения|метод\w* устранения|причин\w* неисправност|внешнее проявление|неисправности и методы")
WHY_RX = re.compile(r"(?i)почему|причин|что делать|в ч[её]м дело|из-за чего|отчего|что провер|неисправ")


def expand(question: str) -> tuple[list[str], bool]:
    """Return ([queries], is_symptom). The first query is always the question itself."""
    low = (question or "").lower()
    maint = [t for rx, t in MAINT if re.search(rx, low)]
    terms = [t for rx, t in SYMPTOMS if re.search(rf"(?<![а-яё])(?:{rx})", low)]
    if maint and MAINT_RX.search(low) and not WHY_RX.search(low.replace("что проверить", "")):
        return [question] + maint, False  # procedure question: its section, no fault-table boost
    symptom = bool(terms) or bool(WHY_RX.search(low))
    queries = [question]
    if terms:
        queries.append(" ".join(terms))  # the symptom alone: context words ("на холостом ходу") must not dominate
    if symptom:
        queries.append("возможные неисправности причины способы устранения " + (" ".join(terms) or question))
    return queries, symptom


def make_boost(queries: list[str]):
    """Boost fault-table pages that also mention the symptom (a generic fault table alone is not enough)."""
    from .bm25 import tokens

    sym = set(tokens(" ".join(queries[1:2]))) - set(tokens("двигатель работа двигателя повышенный посторонний"))

    def boost(page) -> float:
        text = page.text or ""
        b = 1.0
        if sym and sym & set(tokens(text)):
            b *= 1.25
            if FAULT_RX.search(text):
                b *= 1.3
        return b
    return boost
