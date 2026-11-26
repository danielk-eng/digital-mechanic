"""Item 15: draft of a defect inspection (DVI) from this visit's photos (vision descriptions) and the mechanic's
voice/text notes. The draft only restates what was seen or said; each item gets a link to the manual page about
that component (if this engine's manual has one). Nothing is saved until the mechanic confirms the items.
"""

from __future__ import annotations

import json
import re

from .agent.grounding import unverified_numbers

SYSTEM = """Составь черновик акта осмотра по фото и заметкам механика. Верни JSON {"items":[{"component":"узел","finding":"что видно/сказано","severity":"норма|внимание|дефект","source":"фото|заметка"}]}.
Только то, что есть в данных, 1–8 пунктов, коротко, по-русски. Не ставь диагноз, не добавляй цифр, которых нет в данных."""


def collect(agent, c, limit: int = 40) -> list[dict]:
    """Photos and notes since the last check of this vehicle in this conversation."""
    out = []
    for m in agent.store.messages(c.cid, limit):
        meta = json.loads(m.get("meta") or "{}") if isinstance(m.get("meta"), str) else (m.get("meta") or {})
        if meta.get("photo_desc"):
            out.append({"source": "фото", "text": meta["photo_desc"], "photo": meta.get("photo")})
        elif m["role"] == "user" and m.get("content") and not m["content"].startswith(("🔀", "🧪")):
            out.append({"source": "заметка", "text": m["content"].replace("[фото]", "").strip()})
    return [x for x in out if len(x["text"]) > 3][-15:]


def draft(agent, c) -> dict:
    data = collect(agent, c)
    if not data:
        return {"items": [], "note": "Нет фото и заметок по этой машине в текущей сессии: сфотографируйте узел или наговорите, что видите."}
    items = []
    avail = getattr(agent.llm, "available", True)
    if (avail() if callable(avail) else avail):
        try:
            items = agent.llm.json("inspection", SYSTEM, json.dumps(data, ensure_ascii=False)[:8000]).get("items", [])
        except Exception:
            items = []
    if not items:  # no LLM: one item per photo/note, verbatim
        items = [{"component": "", "finding": d["text"][:300], "severity": "внимание", "source": d["source"]} for d in data]
    evidence = [d["text"] for d in data]
    clean = []
    for it in items[:8]:
        f = str(it.get("finding", ""))
        if unverified_numbers(f, evidence, free_max=12):
            f = re.sub(r"\d[\d,.]*", "…", f)  # never introduce numbers that nobody saw or said
        it = {"component": str(it.get("component", ""))[:80], "finding": f[:300], "severity": it.get("severity") if it.get("severity") in ("норма", "внимание", "дефект") else "внимание",
              "source": it.get("source") if it.get("source") in ("фото", "заметка") else "заметка"}
        if it["component"] and c.vehicle:
            hits = agent.index.search(c.vehicle.engine_model, f"{it['component']} проверка осмотр", k=1)
            if hits:
                it.update(doc_id=hits[0].page.doc_id, document=hits[0].page.document, page=hits[0].page.page)
        clean.append(it)
    photos = [d["photo"] for d in data if d.get("photo")]
    return {"items": clean, "photos": photos, "requires_review": True}
