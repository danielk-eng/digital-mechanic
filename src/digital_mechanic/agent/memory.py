"""Memory callbacks: the assistant may recall THIS person's own recent work on THIS truck ("вчера ты менял форсунку на
M333 — как она?"). Rules: only work the person did himself, only on the truck in front of him, only the last 14 days,
at most once per conversation, and never when the person switched memory off in settings."""
from __future__ import annotations

import re
import time

WINDOW_DAYS = 14
# engine systems: the callback is offered only when the question is about the same system as the recent work
# (or about the truck in general). «Зазоры клапанов» after «менял форсунку» is not the moment to ask about it.
SYSTEMS = {
    "fuel": r"форсун|форс\b|форсы|тнвд|топлив|впрыск|распылит",
    "valves": r"клапан|зазор|коромысл|грм|распредвал",
    "belts": r"ремн|ремень|генератор|натяж|шкив",
    "oil": r"масл|смазк|давлени\w* масл",
    "cooling": r"охлажд|антифриз|перегр|радиатор|термостат",
    "start": r"пуск|завод|стартер|аккумулятор",
}


def systems(text: str) -> set[str]:
    low = (text or "").lower()
    return {k for k, rx in SYSTEMS.items() if re.search(rx, low)}


def related(question: str | None, what: str) -> bool:
    q = systems(question or "")
    return not q or bool(q & systems(what))


def _when(days: float) -> str:
    d = int(days)
    return "сегодня" if d <= 0 else "вчера" if d == 1 else f"{d} дн. назад"


def recent_work(store, cfg: dict, user: dict, vehicle_id: str, now: float | None = None) -> list[dict]:
    now = now or time.time()
    out = []
    for w in cfg.get("work") or []:
        if w["uid"] == user.get("id") and w["vehicle_id"] == vehicle_id:
            out.append({"days": w["days_ago"], "what": w["what"], "ref": w.get("ticket_id")})
    for r in store.q("SELECT title, started, status, ticket_id FROM repairs WHERE user_id=? AND vehicle_id=? AND started>? ORDER BY started DESC LIMIT 2",
                     (user.get("id"), vehicle_id, now - WINDOW_DAYS * 86400)):
        out.append({"days": (now - r["started"]) / 86400, "what": f"ремонт «{r['title']}» ({'завершён' if r['status'] == 'done' else 'не закончен'})", "ref": r.get("ticket_id")})
    return sorted([o for o in out if o["days"] <= WINDOW_DAYS], key=lambda o: o["days"])[:2]


def hint(store, cfg: dict, user: dict, vehicle_id: str | None, enabled: bool = True, question: str | None = None) -> tuple[str, list[dict]]:
    if not enabled or not vehicle_id or user.get("role") != "mechanic":
        return "", []
    work = [w for w in recent_work(store, cfg, user, vehicle_id) if related(question, w["what"])]
    if not work:
        return "", []
    w = work[0]
    line = (f"ПАМЯТЬ О СОБСТВЕННОЙ РАБОТЕ ЭТОГО МЕХАНИКА: {_when(w['days'])} он на этой машине {w['what']}"
            + (f" ({w['ref']})" if w.get("ref") else "") + ". Одной короткой "
            "фразой в начале ответа по-дружески спроси, как машина после этого (пример: «Вчера ты менял форсунку на M333 — как она?»). "
            "Один раз за разговор, без подробностей о человеке, без оценок его работы.")
    return line, work
