"""Memory callbacks: the assistant may recall THIS person's own recent work on THIS truck ("вчера ты менял форсунку на
M333 — как она?"). Rules: only work the person did himself, only on the truck in front of him, only the last 14 days,
at most once per conversation, and never when the person switched memory off in settings."""
from __future__ import annotations

import time

WINDOW_DAYS = 14


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


def hint(store, cfg: dict, user: dict, vehicle_id: str | None, enabled: bool = True) -> tuple[str, list[dict]]:
    if not enabled or not vehicle_id or user.get("role") != "mechanic":
        return "", []
    work = recent_work(store, cfg, user, vehicle_id)
    if not work:
        return "", []
    w = work[0]
    line = (f"ПАМЯТЬ О СОБСТВЕННОЙ РАБОТЕ ЭТОГО МЕХАНИКА: {_when(w['days'])} он на этой машине {w['what']}"
            + (f" ({w['ref']})" if w.get("ref") else "") + ". Если это связано с вопросом или с машиной сейчас — одной короткой "
            "фразой в начале ответа по-дружески спроси, как машина после этого (пример: «Вчера ты менял форсунку на M333 — как она?»). "
            "Один раз за разговор, без подробностей о человеке, без оценок его работы. Если не связано — не упоминай.")
    return line, work
