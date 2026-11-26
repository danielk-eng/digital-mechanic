"""How the assistant talks to this person: a versioned style card per user.

Seed: from the role and persona (mechanic veteran / mid / student, manager, engineer; the manager and engineer seeds are
derived from how the customer's people talk at the briefing, docs/СТИЛИ_ПЕРСОН.md). The card evolves after sessions
(every EVOLVE_EVERY messages the model rewrites it from the person's own recent messages) and the user can edit it in
settings ("Как помощник с вами говорит"). Style never changes facts: numbers, pages and checks are the same for everyone.
"""
from __future__ import annotations

import json
import re
import time

EVOLVE_EVERY = 8
MAX_LEN = 700

TONE = ("ТОН: по-человечески, тепло и просто, языком мастерской. Короткие предложения. Без канцелярита и штампов "
        "(не «осуществить», «произвести», «данный», «в целях», «необходимо отметить», «обращаем ваше внимание»). "
        "Глаголы вместо отглагольных существительных: «сними крышку», а не «произвести снятие крышки». "
        "Ссылки на источники и все цифры — как обычно, стиль их не меняет.")

SEEDS = {
    "veteran": ("Говори на «ты», коротко, как с уважаемым старшим коллегой: у него 40 лет с грузовиками, азы и лекции не нужны. "
                "Его словами: ГБЦ, форсы, клапана, топливник. Без техники безопасности и пояснений терминов. "
                "Только точная цифра, порядок одной строкой и страница."),
    "selftaught": ("На «ты», уважительно к практике: руки опытные (15 лет), но теорию и нормативы мог не учить. Всегда давай точные "
                   "моменты затяжки, допуски, давления и страницу руководства. Если то, что он делает по привычке, расходится с руководством, "
                   "мягко поправь: «по книге так-то, потому что…», без упрёков."),
    "student": ("На «ты», спокойно и ободряюще, без снисхождения: он только что из колледжа. По одному шагу за раз, каждый шаг начинай с глагола. "
                "Новый термин — простыми словами один раз. Напоминай про безопасность (заглушить, остыть, упоры). "
                "В конце — как понять, что всё получилось."),
    "manager": ("Обращайся на «вы», уважительно, без лишних слов. Начинай с сути: сколько машин, какая, что с ней, "
                "чем грозит и во сколько обойдётся простой. Дальше — сроки, риски, кто отвечает, следующее действие. "
                "Цифры и даты — всегда; про ML — «модель уверена / не уверена», без внутренней кухни."),
    "engineer": ("На «вы», технически. Что вызвано (сервис, эндпоинт, инструмент), статус, время ответа, что пошло не так, "
                 "какой параметр поменять. Термины: дефектовка, заявка, вердикт, повторный замер, уверенность модели. "
                 "Структурно: «значит, по пунктам», без маркетинга."),
}


FORMALITY = {
    "informal": "Обращайся на «ты», просто, как коллега в цеху.",
    "formal": "Обращайся на «вы», вежливо и официально, полными предложениями, без сленга.",
}
# Onboarding: the same sample question answered both ways (same facts, only the manner differs); the person picks.
ONBOARD_SAMPLE = {
    "question": "Двигатель плохо заводится на холодную. Что проверить?",
    "informal": "Начни с простого: глянь аккумулятор и клеммы, потом свечи накаливания и подачу топлива. "
                "Порядок и нормы — на странице руководства, я её открою.",
    "formal": "Рекомендую начать с проверки аккумуляторной батареи и клемм, затем проверить свечи накаливания и подачу топлива. "
              "Порядок проверки и нормативы приведены в руководстве, ссылку я покажу.",
}


def seed_text(user: dict, formality: str | None = None, persona: str | None = None) -> str:
    base = SEEDS[persona if persona in SEEDS else persona_of(user)]
    if formality in FORMALITY:
        base = re.sub(r"^(Говори|Обращайся)?[^.]*?«(ты|вы)»[^.]*\.\s*", "", base)  # drop the persona's own form of address
        base = FORMALITY[formality] + " " + base
    return base


def persona_of(user: dict) -> str:
    role = user.get("role") or "mechanic"
    if role in ("manager", "engineer"):
        return role
    if user.get("_persona") in SEEDS:
        return user["_persona"]
    return {"expert": "veteran", "experienced": "selftaught", "novice": "student"}.get(user.get("experience") or "experienced", "selftaught")


def card(p: dict, user: dict) -> dict:
    """Current style card {"v", "text", "source", "updated", "history": [...]}; seeds itself on first use."""
    st = p.get("style")
    if not st or not st.get("text"):
        per = p.get("persona") if p.get("persona") in SEEDS else persona_of(user)
        st = {"v": 1, "text": seed_text(user, p.get("formality"), per), "source": "seed", "persona": per, "updated": int(time.time()), "history": []}
        p["style"] = st
    return st


def set_text(p: dict, user: dict, text: str, source: str) -> dict:
    st = card(p, user)
    text = re.sub(r"\s+", " ", (text or "").strip())[:MAX_LEN]
    if not text or text == st["text"]:
        return st
    st["history"] = (st.get("history") or [])[-4:] + [{"v": st["v"], "text": st["text"], "source": st.get("source"), "updated": st.get("updated")}]
    st.update(v=st["v"] + 1, text=text, source=source, updated=int(time.time()))
    return st


def prompt(p: dict, user: dict) -> str:
    return f"{TONE}\nСТИЛЬ ДЛЯ ЭТОГО ЧЕЛОВЕКА (v{card(p, user)['v']}): {card(p, user)['text']}"


EVOLVE_SYSTEM = ("Ты обновляешь карточку стиля общения ИИ-помощника автомеханика с конкретным человеком. Дана текущая карточка и его "
                 "последние сообщения. Перепиши карточку (до 500 знаков, 2–4 предложения, по-русски): обращение (ты/вы), длина ответов, "
                 "его собственные слова и сокращения, что ему важно. Не придумывай черт, которых не видно в сообщениях; если сообщения "
                 "ничего не меняют — верни карточку как есть. Только про стиль: никаких фактов о ремонте, цифр, имён. "
                 "Верни JSON {\"text\": \"...\", \"changed\": true|false}.")


def maybe_evolve(llm, p: dict, user: dict, recent: list[str]) -> bool:
    """After every EVOLVE_EVERY messages: rewrite the card from the person's own messages. A card the user edited stays
    his: the model only appends what it learned, it does not overwrite (source=user keeps priority)."""
    st = card(p, user)
    n = p.get("messages", 0)
    if n == 0 or n % EVOLVE_EVERY or st.get("evolved_at") == n or not recent:
        return False
    st["evolved_at"] = n
    try:
        r = llm.json("style_evolve", EVOLVE_SYSTEM, json.dumps({"card": st["text"], "edited_by_user": st.get("source") == "user",
                                                                "messages": [m[:300] for m in recent[-12:]]}, ensure_ascii=False), max_tokens=500)
    except Exception:  # noqa: BLE001 - style is optional, never break a turn
        return False
    if isinstance(r, dict) and r.get("changed") and r.get("text") and st.get("source") != "user":
        set_text(p, user, r["text"], "model")
        return True
    return False


def public(p: dict, user: dict) -> dict:
    st = card(p, user)
    return {"v": st["v"], "text": st["text"], "source": st.get("source"), "persona": st.get("persona") or persona_of(user),
            "updated": st.get("updated"), "history": [{"v": h["v"], "source": h.get("source")} for h in st.get("history") or []],
            "memory": p.get("memory", True)}
