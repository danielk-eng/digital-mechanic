"""Known contradictions between this engine's manuals (organizers' answers in the task chat, 26.11).

The agent never picks one wording silently: it shows both with documents and pages, marks the contradiction,
gives the conservative safe rule and recommends clarifying with the manufacturer for this engine modification.
Every quote is verbatim from the indexed page (tests/test_contradictions.py checks it against the index).
"""
from __future__ import annotations

import re

KNOWN: list[dict] = [
    {
        "id": "yamz236_valve_cold",
        "engine": "ЯМЗ-236",
        "topic": "Тепловые зазоры клапанов: на каком двигателе регулировать",
        "match": re.compile(r"(зазор\w*[^.?!]{0,40}клапан|клапан\w*[^.?!]{0,40}зазор|тепл\w+ зазор|регулир\w*[^.?!]{0,30}клапан)", re.I),
        "sides": [
            {"doc_id": "YAMZ236-RE", "document": "236-3902150-Б РЭ", "page": 31,
             "quote": "Тепловые зазоры регулировать на холодном двигателе или менее чем через 15 минут после его остановки."},
            {"doc_id": "YAMZ236-RK", "document": "236.01.01 РК", "page": 269,
             "quote": "Проверить и подрегулировать тепловые зазоры клапанного механизма перед началом и по окончании обкатки, но не ранее, чем через 15 мин после остановки двигателя."},
            {"doc_id": "YAMZ236-RK", "document": "236.01.01 РК", "page": 276,
             "quote": "Проверку регулировки зазоров в клапанном механизме производить на холодном двигателе или не ранее, чем через 15 минут после его остановки."},
        ],
        "safe_rule": "Регулировать тепловые зазоры на холодном двигателе. Формулировка РК «не ранее чем через 15 минут» приводится со ссылкой, "
                     "но сама по себе не подтверждает, что двигатель уже остыл.",
        "resolution": "",
    },
    {
        "id": "yamz236_main_bearing_bolts",
        "engine": "ЯМЗ-236",
        "topic": "Болты крышек коренных подшипников: число приёмов и момент",
        "match": re.compile(r"коренн\w*[^.?!]{0,60}(болт|крышк|затяж|момент)|(болт|крышк|затяж|момент)\w*[^.?!]{0,60}коренн", re.I),
        "sides": [
            {"doc_id": "YAMZ236-RK", "document": "236.01.01 РК", "page": 41,
             "quote": "затяжку болтов крепления крышек производить с моментом 421-461 Н*м (43-47 кгс*м) последовательно в два приема"},
            {"doc_id": "YAMZ236-RK", "document": "236.01.01 РК", "page": 248,
             "quote": "затянуть болты крепления крышек коренных подшипников не менее, чем в три приема, моментом силы от 421 до 461 Нм (от 42 до 46 кгс·м)"},
        ],
        "safe_rule": "Затягивать не менее чем в три приёма, окончательный момент — 43–46 кгс·м: этот диапазон укладывается в обе страницы. "
                     "Момент в Н·м на обеих страницах одинаков: 421–461 Н·м.",
        "resolution": "Стр. 41 (раздел 2.8, ремонт блока) сама отсылает: «в соответствии с моментом, указанном в разделе Сборка двигателя». "
                      "Это раздел 5 «Сборка двигателей ЯМЗ» (с. 245–), п. 5.2.9 — с. 248, значит основное правило — с. 248: не менее чем в три приёма. "
                      "Единицы: в Н·м страницы совпадают (421–461), а кгс·м различаются только пересчётом "
                      "(421 Н·м ÷ 9,81 ≈ 42,9 кгс·м; 461 Н·м ÷ 9,81 ≈ 47,0 кгс·м — расчёт, не из руководства).",
    },
    {
        "id": "yamz236_oil_pressure",
        "engine": "ЯМЗ-236",
        "topic": "Давление масла на прогретом двигателе",
        "match": re.compile(r"давлен\w*[^.?!]{0,40}масл|масл\w*[^.?!]{0,40}давлен", re.I),
        "sides": [
            {"doc_id": "YAMZ236-RE", "document": "236-3902150-Б РЭ", "page": 19,
             "quote": "Давление масла на прогретом двигателе должно быть 400-700 кПа (4-7 кгс/см2) при 2100 об/мин"},
            {"doc_id": "YAMZ236-RK", "document": "236.01.01 РК", "page": 267,
             "quote": "должна быть в пределах 400-700 кПа (4-7 кгс/см2) при номинальной частоте вращения"},
            {"doc_id": "YAMZ236-RK", "document": "236.01.01 РК", "page": 12,
             "quote": "400-730 (4-7,3)"},
        ],
        "safe_rule": "Норма 400–700 кПа (4–7 кгс/см²): её дают РЭ с. 19 и РК с. 267. Верхнюю границу 730 кПа из таблицы РК с. 12 "
                     "уточнить у производителя для этой модификации двигателя.",
        "resolution": "",
    },
]


def find(engine_model: str | None, question: str | None) -> list[dict]:
    if not engine_model or not question:
        return []
    return [k for k in KNOWN if engine_model.upper().startswith(k["engine"]) and k["match"].search(question)]


def card(k: dict) -> dict:
    return {"kind": "contradiction", "id": k["id"], "topic": k["topic"], "sides": k["sides"], "safe_rule": k["safe_rule"],
            "resolution": k["resolution"], "clarify": "Рекомендуется уточнить у производителя (ЯМЗ) для конкретной модификации двигателя."}


def context_block(ks: list[dict]) -> str:
    out = []
    for k in ks:
        sides = "; ".join(f"{s['document']} стр. {s['page']}: «{s['quote']}»" for s in k["sides"])
        out.append(f"ПРОТИВОРЕЧИЕ В РУКОВОДСТВАХ ({k['topic']}): {sides}. Безопасное правило: {k['safe_rule']} "
                   + (f"Разбор: {k['resolution']} " if k["resolution"] else "")
                   + "Не выбирай одну формулировку молча: назови обе (интерфейс покажет карточку со страницами), безопасное правило и уточнение у производителя.")
    return "\n".join(out)
