"""Manager briefing: "покажи машину, которую сканировали последней".

Deterministic (no LLM): the last ML session across the fleet -> vehicle card narration (who scanned, when, verdict,
code, history, responsible mechanic) + proactive insights with citations:
  * repeat repair: the same unit (fault code) was repaired on this truck N days ago -> possible repeat failure;
  * similar case: another truck had the same fault code, how it was fixed and whether the re-check was normal;
  * manual pages for the unit on this truck's engine (search in this engine's manuals only).
Then a follow-up ("создать заявку и назначить X?"); a yes creates the TOiR ticket and hands it to the mechanic."""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime

from .. import demo_seed, pii, policy
from ..schemas import Reference, TicketRequest

LAST_RX = re.compile(r"(последн\w*|свеж\w*|недавн\w*)\W+(?:\w+\W+){0,4}?(машин\w*|скан\w*|провер\w*|запис\w*|диагност\w*|замер\w*)"
                     r"|(сканир\w*|провер\w*|записыв\w*)\W+(?:\w+\W+){0,3}?последн\w*", re.I)
YES_RX = re.compile(r"^\W*(да|давай|создай|создавай|создать|оформи|оформляй|ок|окей|хорошо|согласен|подтверждаю|назнач\w*|поехали)\b", re.I)
NO_RX = re.compile(r"^\W*(нет|не надо|не нужно|отмена|отбой|позже|потом)\b", re.I)
REPEAT_DAYS = 90
URG_RU = {"high": "высокая", "medium": "средняя", "low": "низкая"}


def _d(iso: str | None) -> str:
    return f"{iso[8:10]}.{iso[5:7]}" if iso and len(iso) >= 10 else "—"


def _num(x) -> str:
    return f"{x:.2f}".replace(".", ",") if isinstance(x, (int, float)) else "—"


class Briefing:
    def __init__(self, agent, cfg: dict):
        self.a, self.cfg = agent, cfg

    # ------------------------------------------------------------------ facts
    def latest(self):
        best = None
        for v in self.a.fleet.all():
            for s in self.a.fleet.sessions(v.vehicle_id):
                if s.checked_at and (best is None or s.checked_at > best[1].checked_at):
                    best = (v, s)
        return best

    def _next_status(self, vid: str, session_id: str) -> str | None:
        ss = sorted(self.a.fleet.sessions(vid), key=lambda s: s.checked_at or "")
        ids = [s.session_id for s in ss]
        if session_id in ids and ids.index(session_id) + 1 < len(ss):
            n = ss[ids.index(session_id) + 1]
            return getattr(n.status, "value", n.status)
        return None

    def _events(self, vid: str) -> list[dict]:
        """Closed work on a truck: demo history + tickets/repairs made in the app."""
        out = []
        sess = {s.session_id: s for s in self.a.fleet.sessions(vid)}
        for e in self.a.demo.get(vid, []):
            s = sess.get(e["after_session"])
            out.append({"vehicle_id": vid, "ticket_id": e["ticket_id"], "date": e.get("date") or (s.checked_at[:10] if s else ""),
                        "unit": e.get("unit") or (s.anomaly_type if s else None), "mechanic": e.get("mechanic"), "steps": e.get("repair_steps") or [],
                        "parts": e.get("parts") or [], "session_id": e["after_session"], "refs": e.get("refs") or [], "source": "demo_history", "done": e.get("done")})
        for t in self.a.store.tickets(vid):
            p = t.get("payload") or {}
            p = json.loads(p) if isinstance(p, str) else p
            out.append({"vehicle_id": vid, "ticket_id": t["ticket_id"], "date": time.strftime("%Y-%m-%d", time.localtime(t["ts"])), "unit": t.get("anomaly_type"),
                        "mechanic": None, "steps": p.get("operations") or [], "parts": p.get("spare_parts") or [], "session_id": t.get("session_id"),
                        "refs": [{"doc_id": None, "document": r.get("manual"), "page": r.get("pdf_page")} for r in p.get("references") or []], "source": "toir"})
        return out

    def repeat(self, vid: str, unit: str, at: str) -> dict | None:
        """The same unit repaired on this truck within REPEAT_DAYS before `at`."""
        if not unit:
            return None
        day = date.fromisoformat(at[:10])
        prev = [e for e in self._events(vid) if e["unit"] == unit and e["date"] and e["date"] < at[:10]]
        prev = [dict(e, days=(day - date.fromisoformat(e["date"])).days) for e in prev]
        prev = [e for e in prev if e["days"] <= REPEAT_DAYS]
        return max(prev, key=lambda e: e["date"]) if prev else None

    def similar(self, vid: str, unit: str, engine: str) -> list[dict]:
        """Other trucks with the same fault code and closed work on it; same engine first, fixed ones (re-check normal) first."""
        out = []
        for v in self.a.fleet.all():
            if v.vehicle_id == vid:
                continue
            for e in self._events(v.vehicle_id):
                if e["unit"] == unit and e["steps"]:
                    out.append(dict(e, model=v.model or v.make, engine=v.engine_model, same_engine=v.engine_model == engine,
                                    result=self._next_status(v.vehicle_id, e["session_id"])))
        return sorted(out, key=lambda e: (not e["same_engine"], e["result"] != "normal", e["date"]), reverse=False)[:2]

    def manual_refs(self, vehicle, title: str, k: int = 2) -> list[dict]:
        hits = self.a.index.search_multi(vehicle.engine_model, [title, f"система {title.lower()} неисправности проверка"], k=k)
        return [{"document": h.page.document, "doc_id": h.page.doc_id, "page": h.page.page} for h in hits]

    def _account_by_name(self, name: str | None) -> dict | None:
        return next((a for a in demo_seed.people(self.a.store, self.cfg) if name and a["name"] == name), None)

    # ------------------------------------------------------------------ turns
    def route(self, c, user: dict, text: str, turn):
        if c.pending and YES_RX.search(text or ""):
            return self.create(c, user, text, turn)
        if c.pending and NO_RX.search(text or ""):
            c.pending = None
            turn.text = "Хорошо, заявку не создаю. Машина остаётся в списке внимания."
            return turn
        if LAST_RX.search(text or ""):
            return self.brief(c, user, turn)
        return None

    def brief(self, c, user: dict, turn):
        v, s = self.latest()
        vid, unit = v.vehicle_id, s.anomaly_type
        st = getattr(s.status, "value", s.status)
        scan = demo_seed.scanned_by(self.a.store, vid, s.session_id)
        owner = demo_seed.assigned(self.cfg, vid, self.a.store)
        tl = self.a.timeline(vid)
        model = v.model if v.model and v.model.startswith(v.make) else f"{v.make} {v.model or ''}".strip()
        lines = [f"**{vid}, {model} ({v.engine_model}), гараж {v.garage_no}.** Последняя запись — {_d(s.checked_at)} в {s.checked_at[11:16]} (МСК)"
                 + (f", записывал {scan['name'].rstrip('.')}." if scan else ".")]
        if st == "anomaly":
            lines.append(f"**Вердикт:** отклонение {unit} «{s.anomaly_title}», оценка {_num(s.anomaly_score)}, уверенность модели {_num(s.confidence)}, "
                         f"запись {'чистая' if getattr(s.signal_quality, 'value', s.signal_quality) == 'good' else 'с помехами'}. {s.description}.")
        else:
            lines.append(f"**Вердикт:** {'норма' if st == 'normal' else 'запись не годится, нужен повторный замер'}. {s.description}.")
        hist = [e for e in tl["entries"][1:4]]
        if hist:
            lines.append("**История:** " + "; ".join(f"{_d(e['date'])} — {e['status_ru']}{' ' + e['anomaly_type'] if e.get('anomaly_type') else ''}" for e in hist) + ".")
        sources, insights = [], []
        refs = self.manual_refs(v, s.anomaly_title or "") if unit else []
        rep = self.repeat(vid, unit, s.checked_at) if unit else None
        if rep:
            what = rep.get("done") or ", ".join(x.lower() for x in rep["steps"][-1:] or rep["parts"])
            lines.append(f"**Повтор:** этот узел на этой машине уже ремонтировали {rep['days']} дн. назад: {_d(rep['date'])}, {rep['ticket_id']}, "
                         f"{rep['mechanic'] or 'механик'} {what}. Возможен повторный отказ.")
            insights.append({"kind": "repeat", "unit": unit, "days": rep["days"], "ticket_id": rep["ticket_id"], "date": rep["date"],
                             "mechanic": rep["mechanic"], "cite": {"history": rep["source"], "vehicle_id": vid, "ticket_id": rep["ticket_id"]}})
            for r in rep["refs"]:
                if r.get("doc_id"):
                    refs.append({"document": self._doc_title(r["doc_id"]), "doc_id": r["doc_id"], "page": r["page"]})
        sim = self.similar(vid, unit, v.engine_model) if unit else []
        if sim:
            e = sim[0]
            eng = "тот же двигатель" if e["same_engine"] else f"другой двигатель, {e['engine']}"
            res = {"normal": "повторная проверка — норма", "anomaly": "повторная проверка — снова отклонение"}.get(e["result"], "повторной проверки ещё не было")
            lines.append(f"**Похожий случай:** {e['vehicle_id']} ({e['model']}, {eng}), {unit}, {_d(e['date'])}, {e['ticket_id']} — "
                         f"{e['mechanic'] or 'механик'} {e.get('done') or '; '.join(x[0].lower() + x[1:] for x in e['steps'][1:3] or e['steps'][:2])}; {res}.")
            insights.append({"kind": "similar", "unit": unit, "vehicle_id": e["vehicle_id"], "ticket_id": e["ticket_id"], "date": e["date"],
                             "mechanic": e["mechanic"], "result": e["result"], "same_engine": e["same_engine"],
                             "cite": {"history": e["source"], "vehicle_id": e["vehicle_id"], "ticket_id": e["ticket_id"]}})
        seen = set()
        for r in refs:
            if (r["doc_id"], r["page"]) not in seen and len(sources) < 3:
                seen.add((r["doc_id"], r["page"]))
                sources.append({"label": f"S{len(sources) + 1}", "document": r["document"], "doc_id": r["doc_id"], "page": r["page"], "extra": False, "image": None})
        if sources:
            lines.append("По руководству " + v.engine_model + ": " + ", ".join(f"с. {x['page']} [{x['label']}]" for x in sources) + ".")
        if owner:
            lines.append(f"**Ответственный:** {owner['name']} (закреплён за машиной).")
        card = {"kind": "vehicle_brief", "vehicle_id": vid, "session_id": s.session_id, "status": st, "anomaly_type": unit, "anomaly_title": s.anomaly_title,
                "scanned_by": scan["name"] if scan else None, "checked_at": s.checked_at, "owner": owner["name"] if owner else None, "insights": insights}
        turn.cards.append(card)
        turn.sources = sources
        turn.tools.append({"tool": "briefing.latest_scan", "ok": True, "vehicle_id": vid, "insights": [i["kind"] for i in insights]})
        if st == "anomaly" and policy.may_create_ticket(s) and sources:
            fixer = self._account_by_name(sim[0]["mechanic"]) if sim and sim[0]["result"] == "normal" else None
            pick = fixer or owner
            why = (f"он закрыл такой же случай на {sim[0]['vehicle_id']}" if fixer else "он закреплён за этой машиной")
            if pick and owner and pick is not owner:
                why += f", а {owner['name'].rstrip('.')} работал с этой машиной и поможет"
            c.pending = {"vehicle_id": vid, "session_id": s.session_id, "assignee": pick["uid"] if pick else None, "assignee_name": pick["name"] if pick else None,
                         "sources": sources, "repeat": insights[0] if rep else None, "similar": sim[0] if sim else None}
            lines.append(f"**Предлагаю:** создать заявку на дефектовку и назначить {pick.get('name_acc') or pick['name'] if pick else 'механика'}: {why}. Создать?")
            turn.actions = [{"id": "brief_yes", "kind": "send", "label": "Создать и назначить", "text": "Да, создавай"},
                            {"id": "brief_no", "kind": "send", "label": "Не сейчас", "text": "Нет, позже"}]
        turn.text = "\n".join(lines)
        turn.speak = self._speakable(turn.text)
        return turn

    def _doc_title(self, doc_id: str) -> str:
        p = next((p for p in self.a.index.pages if p.doc_id == doc_id), None)
        return p.document if p else doc_id

    @staticmethod
    def _speakable(text: str) -> str:
        t = re.sub(r"\[S\d+\]|\*\*", "", text)
        return re.sub(r"\s+", " ", t).strip()

    def create(self, c, user: dict, text: str, turn):
        p = c.pending
        names = {a["name"].split()[0].lower(): a for a in demo_seed.people(self.a.store, self.cfg) if a["role"] == "mechanic"}
        said = next((a for k, a in names.items() if re.search(k[:-1] if k.endswith(("ов", "ев")) else k, (text or "").lower())), None)
        assignee = said or next((a for a in demo_seed.people(self.a.store, self.cfg) if a["uid"] == p.get("assignee")), None)
        s = next(x for x in self.a.fleet.sessions(p["vehicle_id"]) if x.session_id == p["session_id"])
        u, why = policy.urgency(s)
        src = p["sources"]
        ops = [f"Проверить систему «{s.anomaly_title.lower()}»: клапан, трубку и шланги управляющего разрежения (с. {src[0]['page']})"]
        if p.get("repeat"):
            ops.append(f"Проверить детали, поставленные {_d(p['repeat']['date'])} по {p['repeat']['ticket_id']}: возможен повторный отказ")
        if p.get("similar"):
            ops.append(f"Учесть решение по {p['similar']['vehicle_id']} ({p['similar']['ticket_id']}): " + "; ".join(x.lower() for x in p["similar"]["steps"][1:3]))
        ops.append("Повторная запись звука двигателя после работ")
        concl = (f"{s.anomaly_type} {s.anomaly_title}: {s.description}, оценка {_num(s.anomaly_score)}, уверенность {_num(s.confidence)}."
                 + (f" Узел ремонтировали {p['repeat']['days']} дн. назад ({p['repeat']['ticket_id']})." if p.get("repeat") else ""))
        req = TicketRequest(vehicle_id=p["vehicle_id"], session_id=s.session_id, anomaly_type=s.anomaly_type, conclusion=pii.mask(concl), urgency=u,
                            operations=ops, spare_parts=[], references=[Reference(manual=x["document"], pdf_page=x["page"]) for x in src])
        ack = self.a.toir.create_ticket(req)
        self.a.store.add_ticket(user["id"], req.model_dump(mode="json"), ack.ticket_id)
        summary = f"{concl} Назначено: {assignee['name'] if assignee else '—'}. Основание срочности: {'; '.join(why)}."
        self.a.store.set_ticket_attachments(ack.ticket_id, {"summary": summary, "answer": "", "pages": [{"doc_id": x["doc_id"], "document": x["document"], "page": x["page"]} for x in src],
                                                           "photos": [], "check": s.model_dump(mode="json")})
        if assignee:
            self.a.store.add_handoff(user["id"], p["vehicle_id"], "assign", f"Назначено: {assignee['name']}", summary,
                                     {"assignee_uid": assignee["uid"], "assignee": assignee["name"], "ticket_id": ack.ticket_id, "pages": src, "photos": [], "dialog": [], "items": []})
        c.pending = None
        turn.text = (f"Готово. Заявка **{ack.ticket_id}** создана в ТОиР, срочность {URG_RU.get(u.value, u.value)}. "
                     + (f"Передал {assignee.get('name_dat') or assignee['name']}, он увидит её в приложении на машине {p['vehicle_id']}. " if assignee else "")
                     + f"В заявке {len(ops)} работы и ссылки на с. {', '.join(str(x['page']) for x in src)}.")
        turn.speak = self._speakable(turn.text)
        turn.cards.append({"kind": "ticket_created", "ticket_id": ack.ticket_id, "request": req.model_dump(mode="json"), "assignee": assignee["name"] if assignee else None})
        turn.tools.append({"tool": "toir.create_ticket", "ok": True, "ticket_id": ack.ticket_id})
        return turn
