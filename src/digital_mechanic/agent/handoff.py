"""Session summary for the next shift / the chief mechanic, hand-off package, ticket attachments.

The summary is one LLM call over the stored (already PII-masked) dialog; every number in it must occur in the
dialog, the check data or the cited manual pages, otherwise the sentence is dropped. Without an LLM the summary is
assembled deterministically from the same data.
"""

from __future__ import annotations

import json
import re
import time

from .grounding import cited_labels, unverified_numbers

SUMMARY_PROMPT = """Составь резюме сессии ремонта для следующей смены и главного механика, 5–8 коротких строк, на {lang} языке, с жирными заголовками:
**Машина**, **Проверка**, **Что сделано**, **Что нашли по руководству** (с метками [S#] из диалога), **Что осталось**, **Заявка / ремонт**.
Только факты из данных ниже, ничего не добавляй и не советуй сверх сказанного. Числа — только как в данных."""


def package(agent, c, limit_msgs: int = 40) -> dict:
    """Everything the chief needs: vehicle, check, dialog, cited pages, photos, ticket, repair step."""
    v = c.vehicle
    msgs = agent.store.messages(c.cid, limit_msgs)
    labels, photos = [], []
    for m in msgs:
        for l in cited_labels(m.get("content") or ""):
            if l in c.frags and l not in labels:
                labels.append(l)
        meta = json.loads(m.get("meta") or "{}") if isinstance(m.get("meta"), str) else (m.get("meta") or {})
        if meta.get("photo"):
            photos.append(meta["photo"])
    pages = [{"label": l, "doc_id": c.frags[l]["doc_id"], "document": c.frags[l]["document"], "page": c.frags[l]["page"],
              "extra": bool(c.frags[l].get("extra"))} for l in labels]
    tickets = agent.store.tickets(v.vehicle_id)[:1]
    insp = next((h for h in agent.store.handoffs(vehicle_id=v.vehicle_id, limit=20) if h["kind"] == "inspection"), None)
    rep = agent.store.repair_open(v.vehicle_id)
    return {
        "vehicle": {"vehicle_id": v.vehicle_id, "model": v.model, "engine_model": v.engine_model},
        "check": c.check.model_dump(mode="json") if c.check else None,
        "dialog": [{"role": m["role"], "text": (m.get("content") or "")[:1200]} for m in msgs],
        "pages": pages, "photos": photos,
        "ticket": {"ticket_id": tickets[0]["ticket_id"], "urgency": tickets[0]["urgency"]} if tickets else None,
        "draft": bool(c.draft),
        "inspection": insp["payload"].get("items") if insp else None,
        "repair": {"title": rep["title"], "step": rep["current"] + 1, "steps": len(json.loads(rep["steps"]) if isinstance(rep["steps"], str) else rep["steps"])} if rep else None,
    }


def _plain(p: dict) -> str:
    v, ch = p["vehicle"], p["check"]
    lines = [f"**Машина**: {v['vehicle_id']} · {v.get('model') or ''} · {v['engine_model']}"]
    if ch:
        lines.append(f"**Проверка**: {ch.get('session_id')} — {ch.get('status')} {ch.get('anomaly_type') or ''} {ch.get('anomaly_title') or ''}".strip())
    asked = [d["text"] for d in p["dialog"] if d["role"] == "user"][-3:]
    if asked:
        lines.append("**Что спрашивали**: " + "; ".join(a[:120] for a in asked))
    if p["pages"]:
        lines.append("**Страницы**: " + ", ".join(f"{x['document']} стр. {x['page']}" for x in p["pages"][:6]))
    if p["ticket"]:
        lines.append(f"**Заявка**: {p['ticket']['ticket_id']} ({p['ticket']['urgency']})")
    if p["repair"]:
        lines.append(f"**Ремонт**: {p['repair']['title']}, шаг {p['repair']['step']} из {p['repair']['steps']}")
    return "\n".join(lines)


def summarize(agent, c, lang: str = "русском") -> dict:
    p = package(agent, c)
    evidence = [json.dumps(p["check"] or {}, ensure_ascii=False), json.dumps(p["vehicle"], ensure_ascii=False)]
    evidence += [d["text"] for d in p["dialog"]] + [c.frags[x["label"]]["text"] for x in p["pages"]]
    text, llm = "", False
    avail = getattr(agent.llm, "available", True)
    if (avail() if callable(avail) else avail) and p["dialog"]:
        try:
            msg, _ = agent.llm.chat("session_summary", [
                {"role": "system", "content": SUMMARY_PROMPT.format(lang=lang)},
                {"role": "user", "content": json.dumps({k: p[k] for k in ("vehicle", "check", "dialog", "pages", "ticket", "draft", "repair")}, ensure_ascii=False)[:14000]}],
                max_tokens=700)
            text, llm = (msg.content or "").strip(), True
        except Exception:
            text = ""
    if text:
        bad = unverified_numbers(text, evidence, allowed={p["vehicle"]["vehicle_id"]}, free_max=12)
        if bad:
            parts = re.split(r"(?<=[.!?])\s+|\n", text)
            text = "\n".join(x for x in parts if not any(b in x or b.replace(".", ",") in x for b in bad)).strip()
    if not text:
        text, llm = _plain(p), False
    return {"text": text, "llm": llm, "package": p, "ts": time.time()}
