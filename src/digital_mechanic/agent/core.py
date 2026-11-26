"""Digital Mechanic agent: LLM tool-calling loop over separate tools (ML, RAG, TOiR, history,
repair), with deterministic guards around everything that has consequences.

* The LLM chooses tools by context (TZ F5); policy, urgency, ticket validation and the
  confirmation gate are plain code.
* Every technical claim must cite a fragment label; numbers are checked against the
  retrieved pages; tickets accept only operations backed by verbatim quotes.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import pii, policy
from ..clients import Fleet, MlClient, ServiceError, ToirClient
from ..i18n import LANGS, lang_or_default
from ..retrieval.bm25 import ExtraIndex, ManualIndex, snippet
from ..schemas import CheckResult, CheckStatus, PolicyDecision, Reference, TicketRequest, Urgency, Vehicle
from ..store import Store
from ..history import summary_for_agent as hist_summary, timeline as build_timeline
from .grounding import cited_labels, quote_in_text, unverified_numbers
from .llm import Llm, LlmUnavailable
from .prompts import DETAIL, EXPAND_PROMPT, EXPERIENCE, MANAGER_SYSTEM, MECHANIC_SYSTEM

CONFIRM_RE = re.compile(r"^\s*(да[,.!\s]*)?(подтвержда\w*|отправля\w*|отправь\w*|tasdiqla\w*|тасдиқ\w*|растаймын|xác nhận|पुष्टि)", re.I)
CANCEL_RE = re.compile(r"^\s*(отмен\w*|не надо|не отправля\w*|отбой|bekor|бекор|болдырма\w*|hủy|रद्द)", re.I)
NEXT_RE = re.compile(r"^\s*(готово|сделал\w*|сделано|дальше|следующ\w*|далее|tayyor|keyingi\w*|тайёр|дайын|келесі|xong|tiếp|हो गया|अगला)[\s.!]*$", re.I)
REPEAT_RE = re.compile(r"^\s*(повтори\w*|ещё раз|еще раз|takrorla\w*|такрор\w*|қайтала\w*|lặp lại|दोहरा\w*)[\s.!]*$", re.I)

TOOLS_MECHANIC = [
    {"type": "function", "function": {"name": "get_diagnosis", "description": "Получить результат акустической проверки двигателя этой машины из ML-сервиса (по умолчанию последняя проверка).",
     "parameters": {"type": "object", "properties": {"session_id": {"type": "string", "description": "S-xxxx, необязательно"}}, "required": []}}},
    {"type": "function", "function": {"name": "search_manual", "description": "Поиск по руководствам по эксплуатации и ремонту двигателя ЭТОЙ машины. Возвращает фрагменты страниц с метками S#.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "запрос по-русски терминами руководства"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "get_vehicle_history", "description": "История проверок, заявок и ремонтов этой машины, динамика anomaly_score.",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "prepare_ticket", "description": "Подготовить черновик заявки на дефектовку в ТОиР (только по просьбе механика). Каждая работа и запчасть — с меткой фрагмента S# и дословной цитатой из него. Приоритет считает система.",
     "parameters": {"type": "object", "properties": {
         "conclusion": {"type": "string", "description": "диагноз для заявки, 1-2 предложения по-русски"},
         "operations": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "source": {"type": "string"}, "quote": {"type": "string"}}, "required": ["text", "source", "quote"]}},
         "spare_parts": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "source": {"type": "string"}, "quote": {"type": "string"}}, "required": ["text", "source", "quote"]}}},
         "required": ["conclusion", "operations", "spare_parts"]}}},
    {"type": "function", "function": {"name": "start_repair", "description": "Начать ремонт по шагам: план из 3-12 шагов по руководству этого двигателя, каждый шаг с меткой S# и дословной цитатой.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"},
         "steps": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string", "description": "действие по-русски"}, "source": {"type": "string"}, "quote": {"type": "string"}}, "required": ["text", "source", "quote"]}}},
         "required": ["title", "steps"]}}},
    {"type": "function", "function": {"name": "update_repair", "description": "Отметить прогресс ремонта: done — текущий шаг выполнен; back — вернуться на шаг назад; finish — ремонт завершён; note — заметка механика.",
     "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["done", "back", "finish", "note"]}, "note": {"type": "string"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "search_extra_docs", "description": "Дополнительные руководства по автомобилю той же марки (не база Заказчика): шасси, кабина, тормоза. Не для нормативов двигателя. Метки X#.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]
TOOLS_MANAGER = [
    {"type": "function", "function": {"name": "fleet_overview", "description": "Сводка по парку: готово/внимание/карантин/в ремонте, открытые заявки, приоритетный список.",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_vehicle_history", "description": "История проверок, заявок и ремонтов машины.",
     "parameters": {"type": "object", "properties": {"vehicle_id": {"type": "string"}}, "required": ["vehicle_id"]}}},
]


PROGRESS = {"search_manual": "🔎 Ищу в руководстве", "search_extra_docs": "📗 Доп. руководство", "get_diagnosis": "🎧 Запрашиваю ML-диагностику",
            "get_vehicle_history": "🗂 Смотрю историю машины", "prepare_ticket": "📝 Готовлю заявку", "start_repair": "🛠 Составляю план ремонта",
            "update_repair": "✔ Отмечаю шаг", "fleet_overview": "📊 Сводка по парку"}


@dataclass
class Conv:
    cid: str
    uid: str
    vehicle: Optional[Vehicle] = None
    check: Optional[CheckResult] = None
    decision: Optional[PolicyDecision] = None
    draft: Optional[dict] = None
    frags: dict[str, dict] = field(default_factory=dict)
    n_s: int = 0
    n_x: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Turn:
    text: str = ""
    cards: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    tokens: int = 0
    ok: bool = True

    def as_dict(self) -> dict:
        return {"text": self.text, "cards": self.cards, "sources": self.sources, "tools": self.tools}


def _same(a: str, b: str) -> bool:
    import difflib

    return difflib.SequenceMatcher(None, a[:1500], b[:1500]).ratio() > 0.9


class Agent:
    def __init__(self, fleet: Fleet, ml: MlClient, toir: ToirClient, index: ManualIndex, extra: ExtraIndex | None,
                 llm: Llm, store: Store, engine_docs: dict, max_rounds: int = 5, pii_masking: bool = True):
        self.fleet, self.ml, self.toir, self.index, self.extra = fleet, ml, toir, index, extra
        self.llm, self.store, self.docs, self.max_rounds, self.pii_masking = llm, store, engine_docs, max_rounds, pii_masking
        self.convs: dict[str, Conv] = {}
        self.progress: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._sections = None
        self._extracts: dict[tuple, dict] = {}

    @property
    def sections(self):
        if self._sections is None:
            from ..retrieval.sections import SectionIndex

            self._sections = SectionIndex(list(self.index.pages) + list(self.extra.pages if self.extra else []))
        return self._sections

    def section_extract(self, doc_id: str, page: int, hint: str, question: str = "", lang: str = "русском") -> dict:
        """Short LLM-written extract of the section that contains the cited line; numbers not present in the
        section text are dropped (no page = no number). Cached per (doc, page, hint, question)."""
        key = (doc_id, page, hint[:200], question[:200], lang)
        if key in self._extracts:
            return self._extracts[key]
        sec = self.sections.find(doc_id, page, hint)
        if not sec:
            return {"text": "", "ok": False}
        body = sec.text[:9000]
        try:
            msg, _ = self.llm.chat("section_extract", [
                {"role": "system", "content": "Ты помогаешь механику. Перескажи раздел руководства коротко (2–4 предложения или 3–5 пунктов) "
                 f"на {lang} языке, только по тексту раздела, ничего не добавляя. Если задан вопрос — выжимка отвечает на него. "
                 "Числа, моменты, зазоры, давления переписывай ТОЛЬКО если они есть в тексте, без изменений. Без вступлений."},
                {"role": "user", "content": f"Вопрос механика: {question or '-'}\nЦитата: {hint[:300]}\nРаздел «{sec.label}» (стр. {sec.span}):\n{body}"}],
                max_tokens=500)
            text = (msg.content or "").strip()
        except Exception as e:  # LLM down -> UI still shows the full section
            return {"text": "", "ok": False, "error": type(e).__name__}
        bad = unverified_numbers(text, [body], allowed=set(), free_max=5) if text else []
        if bad:  # drop sentences carrying numbers not found in the section
            parts = re.split(r"(?<=[.!?])\s+|\n", text)
            text = " ".join(x for x in parts if not any(b in x or b.replace(".", ",") in x for b in bad)).strip()
        out = {"text": text, "ok": bool(text), "dropped_numbers": bad}
        self._extracts[key] = out
        return out

    # ------------------------------------------------------------------ state
    def conv(self, uid: str, vehicle_id: str | None) -> Conv:
        cid = f"{uid}:{vehicle_id or '-'}"
        with self._lock:
            c = self.convs.get(cid)
            if c is None:
                c = Conv(cid, uid, self.fleet.get(vehicle_id) if vehicle_id else None)
                self.convs[cid] = c
        return c

    def suggestions(self, c: Conv, user: dict) -> list[dict]:
        """Adaptive chips for the selected vehicle / check / repair step (see agent/suggest.py)."""
        from . import suggest

        if not c.vehicle:
            return []
        hist = self.fleet.sessions(c.vehicle.vehicle_id)
        if c.check:
            hist = [h for h in hist if (h.checked_at or "") <= (c.check.checked_at or "~")]
        chips = [ch.as_dict() for ch in suggest.build(
            self.index, c.vehicle, c.check, hist, self.repair_card(c.vehicle.vehicle_id), c.draft,
            bool(self.store.tickets(c.vehicle.vehicle_id)), user.get("experience") or "experienced")]
        if c.check and c.check.anomaly_type:
            tl = self.timeline(c.vehicle.vehicle_id, until=c.check.checked_at)
            past = [e for e in tl["entries"][1:] if e["anomaly_type"] == c.check.anomaly_type and (e["repair"] or e["ticket"])]
            if past:
                tk = (past[0]["ticket"] or {}).get("ticket_id", past[0]["session_id"])
                chips = [{"text": f"После ремонта по {tk} дефект повторился — что проверить ещё?", "kind": "history", "doc": None, "page": None}] + \
                        [ch for ch in chips if "уже был" not in ch["text"]][:4]
        lang = lang_or_default(user.get("lang"))
        if lang != "ru" and chips and self.llm.available:
            chips = self._translate_chips(chips, lang)
        return chips

    _chip_cache: dict = {}

    def _translate_chips(self, chips: list[dict], lang: str) -> list[dict]:
        """Optional phrasing in the user's language; cached; Russian if the LLM fails."""
        todo = [ch["text"] for ch in chips if (lang, ch["text"]) not in self._chip_cache]
        if todo:
            try:
                from .llm import parse_json

                msg, _ = self.llm.chat("chips_i18n", [{"role": "user", "content":
                    f"Переведи короткие вопросы механика на язык «{LANGS[lang]['llm']}». Термины и цифры сохрани. "
                    "Ответ — только JSON {\"items\": [...]} в том же порядке.\n" + json.dumps(todo, ensure_ascii=False)}],
                    max_tokens=800, temperature=0)
                items = parse_json(msg.content or "").get("items") or []
                if len(items) == len(todo):
                    for ru, tr in zip(todo, items):
                        self._chip_cache[(lang, ru)] = str(tr)[:120]
            except Exception:
                pass
        return [{**ch, "text": self._chip_cache.get((lang, ch["text"]), ch["text"])} for ch in chips]

    def manuals_for(self, engine: str) -> list[dict]:
        return [{"doc_id": k, "title": v["title"], "full_title": v.get("full_title", v["title"]), "kind": v.get("kind")}
                for k, v in self.docs.items() if engine in v["engines"]]

    # ------------------------------------------------------------------ diagnosis (button = direct tool call, F2)
    def diagnose(self, c: Conv, session_id: str | None = None) -> dict:
        v = c.vehicle
        sid = session_id or self.fleet.latest_session_id(v.vehicle_id)
        check = self.ml.diagnose(v.vehicle_id, sid)
        if check.vehicle_id != v.vehicle_id:
            raise ServiceError("ml", 409, "check belongs to another vehicle")
        for s in self.fleet.sessions(v.vehicle_id):
            if s.session_id == sid:
                check.checked_at = s.checked_at
        c.check, c.draft = check, None
        c.decision = policy.decide(check)
        u, why = policy.urgency(check) if check.status == CheckStatus.anomaly else (None, [])
        self.store.add_diagnosis(c.uid, check.model_dump(mode="json"), c.decision.value, u.value if u else None)
        return self.verdict_card(check, c.decision, u, why)

    def verdict_card(self, check: CheckResult, decision: PolicyDecision, u: Urgency | None, why: list[str]) -> dict:
        card = {"kind": "verdict", "status": check.status.value, "decision": decision.value, "check": check.model_dump(mode="json"),
                "doubt": policy.doubt_flags(check) if check.status == CheckStatus.anomaly else []}
        if u:
            code, label = policy.LIGHT[u]
            card.update(urgency=u.value, light=code, light_text=label, urgency_basis=why)
        elif check.status == CheckStatus.normal:
            card.update(light="go", light_text="Норма: можно эксплуатировать, заявка не нужна")
        else:
            card.update(light="repeat", light_text="Сбой проверки: диагноз не ставится, повторите запись")
        card["trend"] = self.trend(check.vehicle_id)
        return card

    def trend(self, vehicle_id: str) -> list[dict]:
        return [{"session_id": s.session_id, "checked_at": s.checked_at, "status": s.status.value, "anomaly_score": s.anomaly_score,
                 "anomaly_type": s.anomaly_type} for s in self.fleet.sessions(vehicle_id)]

    # ------------------------------------------------------------------ context
    def _context(self, c: Conv) -> str:
        lines = []
        v = c.vehicle
        if v:
            lines.append(f"Машина: {v.vehicle_id} (гаражный {v.garage_no}), {v.make} {v.model or ''}, {v.vehicle_type}, двигатель {v.engine_model}, топливо {v.fuel}.")
            lines.append("Руководства этого двигателя: " + "; ".join(f"«{d['title']}» ({d['full_title']})" for d in self.manuals_for(v.engine_model)))
        if c.check:
            ch = c.check
            lines.append(f"Текущая проверка {ch.session_id} от {ch.checked_at}: status={ch.status.value}, тип={ch.anomaly_type} {ch.anomaly_title or ''}, "
                         f"anomaly_score={ch.anomaly_score}, confidence={ch.confidence}, signal_quality={ch.signal_quality.value}, описание модели: «{ch.description}».")
            lines.append(f"Решение политики: {c.decision.value if c.decision else '-'}.")
            if ch.status == CheckStatus.anomaly:
                u, why = policy.urgency(ch)
                lines.append(f"Приоритет (рассчитан системой): {u.value} — {'; '.join(why)}.")
                flags = policy.doubt_flags(ch)
                if flags:
                    lines.append("СОМНЕНИЯ: " + "; ".join(flags) + ". Сначала осмотр узла по руководству, заявка — после подтверждения осмотром.")
        else:
            lines.append("Проверка ещё не запрошена.")
        if v:
            hist = self.fleet.sessions(v.vehicle_id)
            if c.check:  # memory = checks up to the current one (later ones are "future" for this session)
                ids = [h.session_id for h in hist]
                if c.check.session_id in ids:
                    hist = hist[: ids.index(c.check.session_id) + 1]
            tl = self.timeline(v.vehicle_id, until=c.check.checked_at if c.check else None)
            lines.append(f"ИСТОРИЯ МАШИНЫ (до текущей проверки включительно; дней с прошлой проверки: {tl['days_since_last_check']}; пробег — демо):\n" + hist_summary(tl))
            tks = self.store.tickets(v.vehicle_id)
            if tks:
                lines.append("Заявки в ТОиР: " + "; ".join(f"{t['ticket_id']} {t['anomaly_type']} {t['urgency']} {time.strftime('%d.%m %H:%M', time.localtime(t['ts']))}" for t in tks[:5]))
            r = self.store.repair_open(v.vehicle_id)
            if r:
                cur = r["steps"][r["current"]] if r["current"] < len(r["steps"]) else None
                lines.append(f"ИДЁТ РЕМОНТ «{r['title']}»: шаг {r['current'] + 1} из {len(r['steps'])}" + (f" — {cur['text']} ({cur['document']}, стр. {cur['page']})" if cur else ""))
        if c.draft:
            lines.append(f"Черновик заявки ждёт подтверждения механика (приоритет {c.draft['request']['urgency']}).")
        if c.frags:
            lines.append("Уже найденные фрагменты: " + ", ".join(f"{k}={f['document']} стр.{f['page']}" for k, f in list(c.frags.items())[-12:]))
        return "\n".join(lines)

    def _system(self, c: Conv, user: dict, manager: bool = False, detail: str | None = None) -> str:
        lang = LANGS[lang_or_default(user.get("lang"))]["llm"]
        if manager:
            return MANAGER_SYSTEM.format(lang_name=lang, context=f"Сейчас {time.strftime('%d.%m.%Y %H:%M')}.")
        return MECHANIC_SYSTEM.format(lang_name=lang, experience_rule=EXPERIENCE.get(user.get("experience") or "experienced", EXPERIENCE["experienced"]),
                                      length_rule=DETAIL.get(detail or user.get("detail") or "brief", DETAIL["brief"]), context=self._context(c))

    def _expand(self, c: Conv, user: dict, answer: str, turn: "Turn") -> "Turn":
        """Re-render an existing grounded answer in detail: same fragments, same labels, same verification."""
        labels = [l for l in cited_labels(answer) if l in c.frags]
        if not labels:
            turn.text = "Нечего развернуть: в ответе нет ссылок на руководство."
            return turn
        if not self.llm.available:
            turn.text = answer
            turn.sources = [self._source_view(l, c) for l in labels]
            return turn
        frags = "\n\n".join(f"[{l}] {c.frags[l]['document']}, стр. PDF {c.frags[l]['page']}\n{snippet(c.frags[l]['text'], answer, 2500)}" for l in labels[:8])
        messages = [{"role": "system", "content": self._system(c, user, detail="detailed")},
                    {"role": "user", "content": EXPAND_PROMPT.format(answer=answer[:3000], frags=frags)}]
        try:
            msg, tok = self.llm.chat("expand", messages, max_tokens=4500)
            turn.tokens += tok
            out = self._verify_answer(c, messages, (msg.content or "").strip(), turn)
        except LlmUnavailable as e:
            turn.ok = False
            turn.tools.append({"tool": "llm", "error": str(e)})
            out = answer
        turn.tools.append({"tool": "expand", "labels": labels})
        turn.text = out or answer
        turn.sources = [self._source_view(l, c) for l in cited_labels(turn.text) if l in c.frags]
        return turn

    # ------------------------------------------------------------------ tools
    def _add_frag(self, c: Conv, prefix: str, **f: Any) -> str:
        for k, old in c.frags.items():
            if old["doc_id"] == f["doc_id"] and old["page"] == f["page"]:
                return k
        if prefix == "S":
            c.n_s += 1; label = f"S{c.n_s}"
        else:
            c.n_x += 1; label = f"X{c.n_x}"
        c.frags[label] = f
        return label

    def tool_search_manual(self, c: Conv, query: str, k: int = 4) -> dict:
        hits = self.index.search(c.vehicle.engine_model, query, k=k)
        out = []
        for h in hits:
            p = h.page
            lab = self._add_frag(c, "S", doc_id=p.doc_id, document=p.document, page=p.page, text=p.text, extra=False)
            sec = self.sections.find(p.doc_id, p.page, query)
            f = {"label": lab, "document": p.document, "pdf_page": p.page, "text": snippet(p.text, query, 1500)}
            if sec and sec.title:
                f["section"], f["section_pages"] = sec.label[:120], sec.span
            out.append(f)
            # the procedure continues on the next page: give it as its own citable fragment
            nxt = next((x for x in (sec.pages if sec else []) if x["page"] == p.page + 1), None)
            if nxt and len(out) < k + 2:
                np_ = self.index.get_page(p.doc_id, p.page + 1)
                if np_ and not any(o["pdf_page"] == np_.page and o["document"] == np_.document for o in out):
                    lab2 = self._add_frag(c, "S", doc_id=np_.doc_id, document=np_.document, page=np_.page, text=np_.text, extra=False)
                    out.append({"label": lab2, "document": np_.document, "pdf_page": np_.page, "text": snippet(nxt["text"], query, 900),
                                "section": f.get("section", ""), "note": "продолжение того же раздела"})
        return {"engine": c.vehicle.engine_model, "fragments": out} if out else {"engine": c.vehicle.engine_model, "fragments": [], "note": "ничего не найдено"}

    def tool_search_extra(self, c: Conv, query: str) -> dict:
        make = (c.vehicle.make or "").upper()
        if not self.extra or make not in self.extra.makes():
            return {"fragments": [], "note": f"дополнительных руководств для марки {c.vehicle.make} нет"}
        out = []
        for p, _ in self.extra.search(make, query, k=3):
            lab = self._add_frag(c, "X", doc_id=p.doc_id, document=p.document, page=p.page, text=p.text, extra=True, image=p.image)
            out.append({"label": lab, "document": p.document + " [доп. источник, не база Заказчика]", "pdf_page": p.page, "text": snippet(p.text, query, 1500)})
        return {"fragments": out, "caveat": "Дополнительный источник по автомобилю, модели могут отличаться; нормативы двигателя брать только из S#."}

    demo: dict = {}

    def timeline(self, vehicle_id: str, until: str | None = None) -> dict:
        v = self.fleet.get(vehicle_id)
        return build_timeline(v, self.fleet.sessions(vehicle_id), self.store, self.demo, until=until)

    def tool_history(self, vehicle_id: str) -> dict:
        ss = self.fleet.sessions(vehicle_id)
        scores = [s.anomaly_score for s in ss if s.anomaly_score is not None]
        trend = "растёт" if len(scores) >= 2 and scores[-1] - scores[0] > 0.15 else "снижается" if len(scores) >= 2 and scores[0] - scores[-1] > 0.15 else "стабильно"
        return {"vehicle_id": vehicle_id, "timeline": hist_summary(self.timeline(vehicle_id)), "checks": [s.model_dump(mode="json") for s in ss], "anomaly_score_trend": trend,
                "tickets": [{"ticket_id": t["ticket_id"], "urgency": t["urgency"], "anomaly_type": t["anomaly_type"], "conclusion": t["payload"]["conclusion"],
                             "at": time.strftime("%d.%m.%Y %H:%M", time.localtime(t["ts"]))} for t in self.store.tickets(vehicle_id)],
                "repairs": [{"title": r["title"], "status": r["status"], "step": r["current"] + 1, "steps": len(r["steps"])} for r in self.store.q(
                    "SELECT title,status,current,steps FROM repairs WHERE vehicle_id=? ORDER BY id DESC LIMIT 5", (vehicle_id,)) for r in [{**r, "steps": json.loads(r["steps"])}]]}

    def _verify_items(self, c: Conv, items: list[dict], kind: str) -> tuple[list[dict], list[str]]:
        ok, errs = [], []
        for it in items or []:
            lab, quote, text = str(it.get("source", "")).strip("[] "), str(it.get("quote", "")), str(it.get("text", "")).strip()
            f = c.frags.get(lab)
            if not text:
                continue
            if not f or f.get("extra"):
                errs.append(f"{kind} «{text}»: метка {lab or '-'} не из руководства двигателя (нужна S#)")
            elif not quote_in_text(quote, f["text"]):
                errs.append(f"{kind} «{text}»: цитата не найдена дословно на {f['document']} стр. {f['page']}")
            else:
                ok.append({"text": text, "document": f["document"], "doc_id": f["doc_id"], "page": f["page"], "quote": quote[:300], "label": lab})
        return ok, errs

    def tool_prepare_ticket(self, c: Conv, args: dict) -> dict:
        ch = c.check
        if not policy.may_create_ticket(ch):
            why = "проверка не запрошена" if not ch else {"normal": "двигатель в норме — заявка не нужна", "check_failed": "сбой записи — диагноз не ставится, нужна повторная запись"}.get(ch.status.value, "нет отклонения")
            return {"error": f"Заявку создать нельзя: {why}."}
        ops, e1 = self._verify_items(c, args.get("operations") or [], "работа")
        parts, e2 = self._verify_items(c, args.get("spare_parts") or [], "запчасть")
        if not ops:
            return {"error": "Нет ни одной работы с подтверждённой цитатой. Найди процедуру (search_manual) или общую проверку/осмотр узла и повтори.", "rejected": e1 + e2}
        u, why = policy.urgency(ch)
        refs, seen = [], set()
        for it in ops + parts:
            key = (it["document"], it["page"])
            if key not in seen:
                seen.add(key); refs.append(Reference(manual=it["document"], pdf_page=it["page"]))
        conclusion = (args.get("conclusion") or "").strip() or f"{ch.anomaly_title}: {ch.description}"
        req = TicketRequest(vehicle_id=ch.vehicle_id, session_id=ch.session_id, anomaly_type=ch.anomaly_type, conclusion=pii.mask(conclusion),
                            urgency=u, operations=[o["text"] for o in ops], spare_parts=[p["text"] for p in parts], references=refs)
        c.draft = {"id": uuid.uuid4().hex[:8], "request": req.model_dump(mode="json"), "operations": ops, "spare_parts": parts, "urgency_basis": why,
                   "requires_inspection": bool(policy.doubt_flags(ch)), "rejected": e1 + e2}
        return {"draft_ready": True, "urgency": u.value, "operations": len(ops), "spare_parts": len(parts), "rejected": e1 + e2,
                "requires_inspection": c.draft["requires_inspection"], "note": "Черновик показан механику, он подтвердит отправку кнопкой."}

    def tool_start_repair(self, c: Conv, args: dict) -> dict:
        steps, errs = self._verify_items(c, args.get("steps") or [], "шаг")
        if not steps:
            return {"error": "Нет шагов с подтверждённой цитатой из руководства этого двигателя.", "rejected": errs}
        for s in steps:
            s["done"] = False
        tid = (self.store.tickets(c.vehicle.vehicle_id) or [{}])[0].get("ticket_id")
        r = self.store.start_repair(c.uid, c.vehicle.vehicle_id, c.check.session_id if c.check else None, tid, args.get("title") or "Ремонт", steps)
        return {"started": True, "steps": len(steps), "rejected": errs, "current": steps[0]["text"]}

    def repair_action(self, c: Conv, action: str, note: str = "") -> dict:
        r = self.store.repair_open(c.vehicle.vehicle_id)
        if not r:
            return {"error": "ремонт не начат"}
        n = len(r["steps"])
        if action == "done" and r["current"] < n:
            r["steps"][r["current"]]["done"] = True
            r["steps"][r["current"]]["done_at"] = time.time()
            r["current"] += 1
        elif action == "back" and r["current"] > 0:
            r["current"] -= 1
            r["steps"][r["current"]]["done"] = False
        elif action == "note" and note and r["current"] < n:
            r["steps"][r["current"]].setdefault("notes", []).append(pii.mask(note))
        if action == "finish" or r["current"] >= n:
            r["status"], r["finished"] = "done", time.time()
        self.store.save_repair(r)
        return {"status": r["status"], "step": min(r["current"] + 1, n), "steps": n,
                "current": r["steps"][r["current"]]["text"] if r["current"] < n else None}

    def repair_card(self, vehicle_id: str) -> dict | None:
        r = self.store.repair_open(vehicle_id)
        if not r:
            last = self.store.one("SELECT * FROM repairs WHERE vehicle_id=? ORDER BY id DESC LIMIT 1", (vehicle_id,))
            if last and last["status"] == "done":
                return {"kind": "repair", "status": "done", "title": last["title"], "steps": json.loads(last["steps"]), "current": last["current"]}
            return None
        return {"kind": "repair", "status": r["status"], "title": r["title"], "steps": r["steps"], "current": r["current"], "started": r["started"]}

    def fleet_overview(self) -> dict:
        rows, counts = [], {"ready": 0, "attention": 0, "quarantine": 0}
        open_rep = {r["vehicle_id"]: r for r in self.store.repairs("open")}
        tickets = self.store.tickets()
        tk_by_v: dict[str, list] = {}
        for t in tickets:
            tk_by_v.setdefault(t["vehicle_id"], []).append(t)
        for v in self.fleet.all():
            ss = self.fleet.sessions(v.vehicle_id)
            last = ss[-1] if ss else None
            st = policy.fleet_status(last)
            counts[st] += 1
            u, why = policy.urgency(last) if last and last.status == CheckStatus.anomaly else (None, [])
            scores = [s.anomaly_score for s in ss if s.anomaly_score is not None]
            rank = {"quarantine": 0, "attention": 1, "ready": 2}[st]
            rows.append({"vehicle_id": v.vehicle_id, "garage_no": v.garage_no, "model": v.model or v.make, "engine_model": v.engine_model, "status": st,
                         "last": last.model_dump(mode="json") if last else None, "urgency": u.value if u else None, "urgency_basis": why,
                         "scores": scores, "in_repair": v.vehicle_id in open_rep, "tickets": len(tk_by_v.get(v.vehicle_id, [])),
                         "_rank": (rank, -(last.anomaly_score or 0) if last else 0)})
        rows.sort(key=lambda r: r.pop("_rank"))
        return {"counts": {**counts, "in_repair": len(open_rep), "tickets": len(tickets), "total": len(rows)}, "vehicles": rows}

    # ------------------------------------------------------------------ chat turn
    def handle(self, c: Conv, user: dict, text: str, image_note: str | None = None, event: str | None = None) -> Turn:
        with c.lock:
            return self._handle(c, user, text, image_note, event)

    def _handle(self, c: Conv, user: dict, text: str, image_note: str | None, event: str | None) -> Turn:
        turn = Turn()
        self.progress[c.cid] = []
        manager = user.get("role") == "manager" and c.vehicle is None
        raw = (text or "").strip()
        masked = pii.mask(raw) if self.pii_masking else raw
        if event == "expand":
            return self._expand(c, user, raw, turn)
        # deterministic fast paths (voice-friendly, no LLM)
        if c.vehicle and not event:
            if c.draft and CONFIRM_RE.match(raw):
                return self._confirm_turn(c, user, inspection_ok=True, turn=turn)
            if c.draft and CANCEL_RE.match(raw):
                c.draft = None
                turn.text = "Заявка отменена, в ТОиР ничего не отправлено."
                return turn
            if self.store.repair_open(c.vehicle.vehicle_id) and (NEXT_RE.match(raw) or REPEAT_RE.match(raw)):
                res = self.repair_action(c, "done") if NEXT_RE.match(raw) else self.repair_action(c, "note", "")
                card = self.repair_card(c.vehicle.vehicle_id)
                if card:
                    turn.cards.append(card)
                if res.get("status") == "done":
                    turn.text = "Ремонт завершён. Все шаги выполнены."
                elif res.get("current"):
                    st = card["steps"][card["current"]] if card else {}
                    turn.text = f"Шаг {res['step']} из {res['steps']}: {res['current']} ({st.get('document')}, стр. {st.get('page')})"
                    turn.sources = [self._source_view(st.get("label"), c)] if st.get("label") in c.frags else []
                return turn
        if event == "explain_diagnosis" and c.check:
            if c.check.status == CheckStatus.check_failed or c.check.signal_quality.value == "bad":
                turn.text = (f"Сбой проверки ({c.check.description}). Выводов о двигателе не делаю, заявку не создаю. "
                             "Повторите запись: двигатель на холостом ходу, микрофон под двигателем, 20–30 секунд, без посторонних шумов.")
                return turn
            if c.check.status == CheckStatus.normal:
                turn.text = "Норма: значимых отклонений по звуку нет. Машину можно эксплуатировать, заявка не нужна. Следующая проверка — по графику ТО."
                return turn
            ch = c.check
            pre = []
            for q in (f"{ch.anomaly_title} {ch.description}", f"неисправности причины способы устранения {ch.anomaly_title}"):
                r = self.tool_search_manual(c, q, k=4)
                turn.tools.append({"tool": "search_manual", "args": {"query": q}, "ok": True, "auto": True,
                                   "hits": [f"{f['label']}:{f['document']} стр.{f['pdf_page']}" for f in r["fragments"]]})
                pre += [f for f in r["fragments"] if f["label"] not in {x["label"] for x in pre}]
            frag_txt = "\n\n".join(f"[{f['label']}] {f['document']}, стр. PDF {f['pdf_page']}\n{f['text']}" for f in pre[:7])
            masked = ("Объясни механику результат текущей проверки (до 12 строк): вердикт; что вероятно не так; чем грозит; что и как проверить "
                      "(допуски, порядок со страницами); в конце предложи создать заявку. Ниже — уже найденные фрагменты руководства этого двигателя; "
                      "если в них нет нужного — сделай не более одного дополнительного поиска.\n\n" + frag_txt)
        if image_note:
            masked = (masked + "\n" if masked else "") + f"[Фото от механика, описание: {image_note}]"
        if not self.llm.available:
            return self._fallback(c, masked, turn)

        messages = [{"role": "system", "content": self._system(c, user, manager)}]
        for m in self.store.messages(c.cid, 12):
            if m["role"] in ("user", "assistant") and m["content"]:
                messages.append({"role": m["role"], "content": m["content"][:2500]})
        if len(messages) > 1 and not event:
            # without this marker non-thinking models sometimes re-answer the previous question
            messages.append({"role": "user", "content": "Новый вопрос механика — ответь именно на него (прошлые ответы не повторяй):\n" + masked})
        else:
            messages.append({"role": "user", "content": masked})
        tools = TOOLS_MANAGER if manager else TOOLS_MECHANIC
        if not manager and not (self.extra and (c.vehicle.make or "").upper() in self.extra.makes()):
            tools = [t for t in tools if t["function"]["name"] != "search_extra_docs"]
        answer = ""
        try:
            for rnd in range(self.max_rounds + 1):
                msg, tok = self.llm.chat(f"agent_r{rnd}", messages, tools=tools if rnd < self.max_rounds else None, max_tokens=4500)
                turn.tokens += tok
                calls = getattr(msg, "tool_calls", None) or []
                if not calls:
                    answer = (msg.content or "").strip()
                    prev = [m["content"] for m in messages[1:-1] if m["role"] == "assistant" and m.get("content")]
                    if answer and prev and _same(answer, prev[-1]) and rnd < self.max_rounds:
                        # non-thinking models sometimes replay the previous reply in long chats
                        messages.append({"role": "assistant", "content": answer})
                        messages.append({"role": "user", "content": "Это ответ на прошлый вопрос. Ответь на последний вопрос механика: " + (masked or "")[:500]})
                        turn.tools.append({"tool": "repeat_guard"})
                        answer = ""
                        continue
                    break
                messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in calls]})
                for tc in calls:
                    res = self._run_tool(c, user, tc.function.name, tc.function.arguments, turn)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(res, ensure_ascii=False)[:12000]})
            answer = self._verify_answer(c, messages, answer, turn)
        except LlmUnavailable as e:
            turn.ok = False
            turn.tools.append({"tool": "llm", "error": str(e)})
            return self._fallback(c, masked, turn)
        turn.text = answer or "Не удалось сформировать ответ. Повторите вопрос."
        turn.sources = [self._source_view(l, c) for l in cited_labels(turn.text) if l in c.frags]
        if c.draft and any(t["tool"] == "prepare_ticket" and t.get("ok") for t in turn.tools):
            turn.cards.append(self.draft_card(c))
        if any(t["tool"] in ("start_repair", "update_repair") for t in turn.tools) and c.vehicle:
            card = self.repair_card(c.vehicle.vehicle_id)
            if card:
                turn.cards.append(card)
        return turn

    def _run_tool(self, c: Conv, user: dict, name: str, arguments: str, turn: Turn) -> dict:
        t0 = time.time()
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        try:
            if name == "fleet_overview":
                res = self.fleet_overview()
                res = {"counts": res["counts"], "top": [{k: r[k] for k in ("vehicle_id", "model", "engine_model", "status", "urgency", "in_repair", "tickets")} | {"last_type": (r["last"] or {}).get("anomaly_title"), "score": (r["last"] or {}).get("anomaly_score")} for r in res["vehicles"][:12]]}
            elif name == "get_vehicle_history":
                vid = args.get("vehicle_id") or (c.vehicle.vehicle_id if c.vehicle else "")
                res = self.tool_history(vid) if vid in self.fleet.ids() else {"error": "неизвестная машина"}
            elif c.vehicle is None:
                res = {"error": "машина не выбрана"}
            elif name == "get_diagnosis":
                card = self.diagnose(c, args.get("session_id") or None)
                turn.cards.append(card)
                res = {"check": card["check"], "decision": card["decision"], "urgency": card.get("urgency"), "urgency_basis": card.get("urgency_basis"), "doubt": card["doubt"]}
            elif name == "search_manual":
                n = sum(1 for t in turn.tools if t["tool"] == "search_manual")
                res = self.tool_search_manual(c, str(args.get("query", ""))) if n < 3 else {"note": "Лимит поиска на один ответ исчерпан: отвечай по уже найденным фрагментам или скажи, что данных нет."}
            elif name == "search_extra_docs":
                res = self.tool_search_extra(c, str(args.get("query", "")))
            elif name == "prepare_ticket":
                res = self.tool_prepare_ticket(c, args)
            elif name == "start_repair":
                res = self.tool_start_repair(c, args)
            elif name == "update_repair":
                res = self.repair_action(c, args.get("action", "done"), args.get("note", ""))
            else:
                res = {"error": f"нет инструмента {name}"}
        except ServiceError as e:
            res = {"error": f"сервис {e.service} недоступен или ответил {e.status}: {e.detail[:200]}"}
        except KeyError as e:
            res = {"error": f"не найдено: {e}"}
        self.progress.setdefault(c.cid, []).append(PROGRESS.get(name, name) + (f": {args.get('query')}" if args.get("query") else ""))
        log = {"tool": name, "args": {k: (v if len(json.dumps(v, ensure_ascii=False)) < 300 else "…") for k, v in args.items()}, "ms": int((time.time() - t0) * 1000), "ok": "error" not in res}
        if "fragments" in res:
            log["hits"] = [f"{f['label']}:{f['document']} стр.{f['pdf_page']}" for f in res["fragments"]]
        if "error" in res:
            log["error"] = res["error"][:200]
        turn.tools.append(log)
        return res

    def _verify_answer(self, c: Conv, messages: list[dict], answer: str, turn: Turn) -> str:
        if not answer:
            return answer
        # drop citations to labels that do not exist
        for lab in cited_labels(answer):
            if lab not in c.frags:
                answer = answer.replace(f"[{lab}]", "")
        evidence = [f["text"] for f in c.frags.values()]
        allowed: set[str] = set()
        if c.check:
            from .grounding import numbers
            allowed |= numbers(json.dumps(c.check.model_dump(mode="json"), ensure_ascii=False))
        if c.vehicle:
            from .grounding import numbers
            allowed |= numbers(c.vehicle.model_dump_json())
        for t in self.store.tickets(c.vehicle.vehicle_id) if c.vehicle else []:
            allowed |= {t["ticket_id"].split("-")[-1].lstrip("0")}
        if c.draft:
            allowed |= {str(len(c.draft["operations"])), str(len(c.draft["spare_parts"]))}
        from .grounding import numbers as _nums
        for m in messages:  # numbers the mechanic typed himself may be echoed back
            if m.get("role") == "user" and isinstance(m.get("content"), str) and not m["content"].startswith("Проверка:"):
                allowed |= _nums(m["content"])
        bad = unverified_numbers(answer, evidence, allowed)
        if bad and self.llm.available:
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": f"Проверка: числа {', '.join(bad)} не найдены во фрагментах руководства. Перепиши ответ: оставь только числа из фрагментов с метками [S#], остальные убери или скажи, что данных нет. Без вступлений."})
            try:
                msg, tok = self.llm.chat("agent_fix_numbers", messages, max_tokens=3000)
                turn.tokens += tok
                fixed = (msg.content or "").strip()
                if fixed:
                    answer = fixed
            except LlmUnavailable:
                pass
            bad = unverified_numbers(answer, evidence, allowed)
        if bad:
            sents = re.split(r"(?<=[.!?\n])\s+", answer)
            answer = " ".join(s for s in sents if not unverified_numbers(s, evidence, allowed)).strip()
            answer += "\n\n⚠️ Часть цифр не подтверждена руководством и убрана — уточните по странице."
            turn.tools.append({"tool": "number_guard", "removed": bad})
        return answer

    def _source_view(self, label: str, c: Conv) -> dict:
        f = c.frags[label]
        return {"label": label, "document": f["document"], "doc_id": f["doc_id"], "page": f["page"], "extra": bool(f.get("extra")), "image": f.get("image")}

    def _fallback(self, c: Conv, text: str, turn: Turn) -> Turn:
        """No LLM: extractive answer (top pages of this engine's manuals), never invented text."""
        if not c.vehicle:
            turn.text = "LLM недоступна. Откройте панель парка."
            return turn
        res = self.tool_search_manual(c, text)
        if not res["fragments"]:
            turn.text = "В руководстве для этого двигателя данных не нашёл. Рекомендую дефектовку у специалиста."
            return turn
        lines = ["LLM временно недоступна — показываю найденные страницы руководства:"]
        for f in res["fragments"][:3]:
            lines.append(f"[{f['label']}] {f['document']}, стр. {f['pdf_page']}: {f['text'][:400].strip()}…")
        turn.text = "\n\n".join(lines)
        turn.sources = [self._source_view(f["label"], c) for f in res["fragments"][:3]]
        return turn

    # ------------------------------------------------------------------ ticket confirmation gate
    def draft_card(self, c: Conv) -> dict:
        d = c.draft
        return {"kind": "ticket_draft", "draft_id": d["id"], "request": d["request"], "operations": d["operations"], "spare_parts": d["spare_parts"],
                "urgency_basis": d["urgency_basis"], "requires_inspection": d["requires_inspection"]}

    def confirm(self, c: Conv, draft_id: str | None, inspection_ok: bool = True) -> dict:
        if not c.draft or (draft_id and draft_id != c.draft["id"]):
            return {"error": "нет черновика для подтверждения"}
        if not inspection_ok:
            c.draft = None
            return {"cancelled": True, "text": "Осмотр не подтвердил неисправность — заявка не отправлена, машина может вернуться на линию. Результат записан в журнал."}
        if not policy.may_create_ticket(c.check):
            c.draft = None
            return {"error": "по текущей проверке заявка не допускается"}
        req = TicketRequest(**c.draft["request"])
        ack = self.toir.create_ticket(req)
        self.store.add_ticket(c.uid, c.draft["request"], ack.ticket_id)
        card = {"kind": "ticket_created", "ticket_id": ack.ticket_id, "request": c.draft["request"]}
        c.draft = None
        return {"ticket_id": ack.ticket_id, "card": card, "text": f"✅ Заявка {ack.ticket_id} создана в ТОиР."}

    def _confirm_turn(self, c: Conv, user: dict, inspection_ok: bool, turn: Turn) -> Turn:
        try:
            r = self.confirm(c, None, inspection_ok)
        except ServiceError as e:
            turn.text, turn.ok = f"ТОиР не принял заявку ({e.status}). Черновик сохранён, повторите позже.", False
            return turn
        turn.text = r.get("text") or r.get("error", "")
        if r.get("card"):
            turn.cards.append(r["card"])
        turn.tools.append({"tool": "toir.create_ticket", "ok": "ticket_id" in r, "ticket_id": r.get("ticket_id")})
        return turn
