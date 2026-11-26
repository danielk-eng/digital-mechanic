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
    (r"холост", "работа двигателя на холостом ходу неустойчивая"),
    (r"заряд|генератор|аккумулятор", "генератор не заряжает аккумуляторная батарея"),
]

FAULT_RX = re.compile(r"(?is)(неисправност|внешнее проявление).{0,400}(причин|способ устранения|метод устранения)|"
                      r"(причин\w* неисправност).{0,400}(устранен)")
WHY_RX = re.compile(r"(?i)почему|причин|что делать|в ч[её]м дело|из-за чего|отчего|что провер|неисправ")


def expand(question: str) -> tuple[list[str], bool]:
    """Return ([queries], is_symptom). The first query is always the question itself."""
    low = (question or "").lower()
    terms = [t for rx, t in SYMPTOMS if re.search(rf"(?<![а-яё])(?:{rx})", low)]
    symptom = bool(terms) or bool(WHY_RX.search(low))
    queries = [question]
    if terms:
        queries.append(question + " " + " ".join(terms))
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
                b *= 1.25
        return b
    return boost
