"""Adaptive suggested questions (chips) for the mechanic.

Built deterministically from the vehicle card, the current check (status, fault type, score, confidence), the
vehicle history, the open repair step and the user's expertise level. Every manual question is kept only if the
manual of THIS engine actually has a page about it (BM25 top hit must contain most of the query terms), so a chip
never leads to "no data". Wording for non-Russian users is optional (LLM translation, cached; Russian fallback).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..retrieval.bm25 import tokens

# fault code -> manual topics: (search query in manual terms, question, terse expert form)
TOPICS: dict[str, list[tuple[str, str, str]]] = {
    "F01": [("гидротолкатель стук проверка", "Как проверить гидротолкатели на стук?", "Гидротолкатели: проверка"),
            ("тепловой зазор клапанов коромысло", "Какие тепловые зазоры клапанов на {engine}?", "Зазоры клапанов {engine}"),
            ("регулировка зазоров клапанов порядок", "Порядок регулировки зазоров клапанов", "Регулировка зазоров: порядок"),
            ("момент затяжки болтов осей коромысел", "Момент затяжки осей коромысел", "Момент: оси коромысел")],
    "F02": [("давление масла холостой ход номинальное", "Какое давление масла должно быть на {engine}?", "Давление масла: норма"),
            ("масляный насос редукционный клапан проверка", "Как проверить масляный насос и редукционный клапан?", "Масляный насос / редукционный клапан"),
            ("замена масляного фильтра", "Как заменить масляный фильтр?", "Замена масляного фильтра")],
    "F03": [("термостат проверка температура открытия", "Как проверить термостат?", "Термостат: начало открытия"),
            ("момент затяжки болтов головки цилиндров порядок", "Момент и порядок затяжки болтов головки цилиндров", "Момент: болты ГБЦ"),
            ("охлаждающая жидкость в масле прокладка головки", "Что проверить при попадании ОЖ в масло?", "ОЖ в масле: проверки")],
    "F04": [("натяжение ремня прогиб", "Как проверить натяжение ремня привода?", "Натяжение ремня: прогиб"),
            ("замена ремня привода", "Как заменить ремень привода агрегатов?", "Замена ремня привода")],
    "F05": [("генератор проверка напряжение", "Как проверить генератор?", "Генератор: проверка"),
            ("натяжение ремня генератора", "Натяжение ремня генератора", "Ремень генератора: прогиб")],
    "F06": [("свободный ход педали сцепления регулировка", "Как отрегулировать свободный ход педали сцепления?", "Свободный ход педали сцепления"),
            ("сцепление неисправности причины", "Возможные неисправности сцепления", "Сцепление: неисправности")],
    "F07": [("коробка передач масло уровень", "Какое масло и уровень в коробке передач?", "КП: масло, уровень")],
    "F08": [("угол опережения впрыскивания топлива установка", "Как проверить угол опережения впрыскивания?", "Угол опережения впрыскивания"),
            ("форсунка давление начала впрыскивания проверка", "Как проверить форсунки и давление впрыска?", "Форсунки: давление впрыска"),
            ("топливный насос высокого давления неисправности", "Неисправности ТНВД и их признаки", "ТНВД: неисправности")],
    "F09": [("турбокомпрессор проверка люфт ротора", "Как проверить турбокомпрессор?", "ТКР: люфт ротора"),
            ("давление наддува", "Какое должно быть давление наддува?", "Давление наддува")],
    "F10": [("компрессор пневматической системы проверка", "Как проверить компрессор пневмосистемы?", "Компрессор: проверка"),
            ("насос гидроусилителя руля", "Как проверить насос гидроусилителя?", "Насос ГУР")],
    "F11": [("рециркуляция отработавших газов клапан проверка", "Как проверить клапан рециркуляции газов?", "Клапан EGR: проверка")],
    "F12": [("свечи зажигания зазор между электродами", "Какой зазор свечей зажигания?", "Зазор свечей"),
            ("система зажигания проверка катушка", "Как проверить систему зажигания?", "Зажигание: проверка")],
    "F13": [("давление топлива в рампе", "Какое давление топлива должно быть?", "Давление топлива"),
            ("электробензонасос проверка", "Как проверить бензонасос?", "Бензонасос: проверка")],
    "F14": [("цепь привода распределительных валов натяжитель", "Как проверить цепь ГРМ и гидронатяжитель?", "Цепь ГРМ / натяжитель"),
            ("установка распределительных валов по меткам", "Как выставить метки ГРМ?", "Метки ГРМ")],
}
SERVICE = [("техническое обслуживание периодичность ТО-1 ТО-2", "Что входит в ближайшее ТО?", "Регламент ТО"),
           ("замена масла в двигателе", "Как заменить масло в двигателе?", "Замена масла: объём"),
           ("тепловой зазор клапанов", "Тепловые зазоры клапанов на {engine}", "Зазоры клапанов")]
SAFETY_Q = "Какие меры безопасности при этой работе?"


@dataclass
class Chip:
    text: str
    kind: str  # manual | action | repair | history
    doc: str | None = None
    page: int | None = None

    def as_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind, "doc": self.doc, "page": self.page}


def grounded_page(index, engine: str, query: str):
    """Top page of this engine's manual if it really covers the query (most query terms present), else None."""
    q = set(tokens(query))
    key = list(dict.fromkeys(tokens(query)))[:3]  # leading terms must co-occur within a few lines
    need = max(2, int(len(q) * 0.6 + 0.5)) if len(q) > 2 else len(q)
    for h in index.search(engine, query, k=3):
        if len(q & set(tokens(h.page.text))) < need:
            continue
        lines = h.page.text.split("\n")
        for i in range(len(lines)):
            win = set(tokens(" ".join(lines[i: i + 4])))
            if set(key[:2]) <= win and len(q & win) >= need:
                return h.page
    return None


HYDRO = re.compile(r"гидравлическ\w* толкател|гидротолкател|гидрокомпенсатор", re.I)
_hydro_cache: dict[str, bool] = {}


def hydraulic_lifters(index, engine: str) -> bool:
    """Engine with hydraulic tappets: no valve clearance adjustment, so clearance chips are wrong."""
    if engine not in _hydro_cache:
        _hydro_cache[engine] = any(HYDRO.search(h.page.text) for h in index.search(engine, "гидравлические толкатели клапанов", k=3))
    return _hydro_cache[engine]


# Start of a visit without a check: symptom or fault code first, no long interview (Bosch / Mercedes style).
SYMPTOMS = [("стук двигатель", "Стучит двигатель — что проверить?"),
            ("давление масла низкое", "Низкое давление масла — что проверить?"),
            ("перегрев двигателя охлаждающей", "Перегревается — что проверить?"),
            ("дымление дым отработавших", "Сильно дымит — в чём причина?"),
            ("пуск двигателя затруднен", "Плохо заводится — что проверить?"),
            ("двигатель не развивает мощность", "Не тянет, падает мощность — что проверить?")]
CODE_CHIP = "Ввести код ошибки F…"


def build(index, vehicle, check, history: list, repair: dict | None, draft: dict | None, has_ticket: bool,
          experience: str = "experienced", limit: int = 5) -> list[Chip]:
    eng = vehicle.engine_model
    expert, novice = experience == "expert", experience == "novice"
    chips: list[Chip] = []

    def manual(topics):
        for q, ques, short in topics:
            if (("зазор" in q and "клапан" in q) or "коромыс" in q) and hydraulic_lifters(index, eng):
                continue
            p = grounded_page(index, eng, q)
            if p:
                text = (short if expert else ques).format(engine=eng)
                if expert:
                    text += f" · стр. {p.page}"
                chips.append(Chip(text, "manual", p.doc_id, p.page))

    # 1) open repair: the current step comes first
    if repair and repair.get("status") == "open":
        steps, cur = repair.get("steps") or [], repair.get("current", 0)
        if cur < len(steps):
            st = steps[cur].get("text", "") if isinstance(steps[cur], dict) else str(steps[cur])
            chips.append(Chip("Готово, следующий шаг", "repair"))
            if re.search(r"затя|момент|болт|гайк", st, re.I):
                chips.append(Chip("Какой момент затяжки на этом шаге?" if not expert else "Момент для этого шага", "repair"))
            if re.search(r"зазор|допуск|износ|размер", st, re.I):
                chips.append(Chip("Какой допуск на этом шаге?" if not expert else "Допуск для этого шага", "repair"))
            chips.append(Chip(f"Подробнее про шаг {cur + 1}" if not expert else f"Шаг {cur + 1}: детали", "repair"))
            if novice:
                chips.append(Chip(SAFETY_Q, "repair"))
    status = getattr(check.status, "value", check.status) if check else None
    # 2) the current check result
    if status == "check_failed":
        chips.append(Chip("Почему запись не принята и как перезаписать?", "action"))
    elif status == "anomaly":
        title = f"«{check.anomaly_title or check.anomaly_type}»"
        if (check.confidence is not None and check.confidence < 0.6) or getattr(check.signal_quality, "value", check.signal_quality) == "warning":
            chips.append(Chip(f"Что осмотреть, чтобы подтвердить {title}?", "action"))
        if not expert:
            chips.append(Chip(f"Чем грозит неисправность {title}?", "action"))
        manual(TOPICS.get(check.anomaly_type or "", []))
        if draft:
            chips.append(Chip("Подтверждаю заявку", "action"))
        elif not has_ticket and not (repair and repair.get("status") == "open"):
            chips.append(Chip("Создать заявку на дефектовку", "action"))
        elif not (repair and repair.get("status") == "open"):
            chips.append(Chip("Начать ремонт по шагам", "action"))
    elif status == "normal":
        manual(SERVICE)
    elif status is None and not (repair and repair.get("status") == "open"):
        for q, text in SYMPTOMS:
            p = grounded_page(index, eng, q)
            if p and len(chips) < 4:
                chips.append(Chip(text, "symptom", p.doc_id, p.page))
        chips.append(Chip(CODE_CHIP, "code"))
    # 3) history: repeated fault or growing score
    prev = [h for h in history if getattr(h.status, "value", h.status) == "anomaly" and (not check or h.session_id != check.session_id)]
    if check and status == "anomaly" and any(h.anomaly_type == check.anomaly_type for h in prev):
        chips.append(Chip("Этот дефект уже был — что делали в прошлый раз?", "history"))
    elif len(history) >= 3:
        chips.append(Chip("Динамика anomaly_score по проверкам", "history"))
    if novice and status == "anomaly":
        chips.append(Chip(f"Объясни простыми словами: {title}", "action"))
    if len({c.text for c in chips}) < 3 and status in ("anomaly", "normal"):
        manual(SERVICE if status == "anomaly" else [])  # fill with engine-specific service topics
    if len({c.text for c in chips}) < 3 and history:
        chips.append(Chip("Динамика anomaly_score по проверкам", "history"))
    # de-duplicate, keep order, cap
    seen, out = set(), []
    for ch in chips:
        if ch.text not in seen:
            seen.add(ch.text); out.append(ch)
    return out[:limit] if len(out) >= 3 else out
