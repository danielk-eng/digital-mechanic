"""Mechanic agent: fixed pipeline with tool calls, deterministic policy, grounded answers, trace.

LLM is used only to (1) extract works/parts from retrieved manual pages and (2) answer
mechanic's questions from retrieved pages. Both outputs pass a verbatim-quote check.
Policy, priority, ticket validation and the confirm gate are plain code.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import policy
from ..clients import Fleet, MlClient, ToirClient
from ..priority import compute_priority, doubt_flags
from ..retrieval.bm25 import ManualIndex
from ..schemas import (CheckResult, CheckStatus, PolicyDecision, Priority, SourcePage, TicketDraft, Vehicle)
from .grounding import (Extraction, extract_fallback, extract_with_llm, make_fragments, norm, quote_in_page)
from .llm import Llm, LlmUnavailable

CONFIRM_RE = re.compile(r"^\s*(да[,.! ]*)?(подтвержда\w*|отправ\w*( заявку)?|оформ\w*|создай\w*( заявку)?)\b", re.I)
CANCEL_RE = re.compile(r"^\s*(отмен\w*|не надо|не отправляй\w*|отбой)\b", re.I)
CHECK_RE = re.compile(r"(результат|провер\w+|диагност\w+).*(двигат|запис|машин|получ|покаж)|^(проверь|диагностика)", re.I)

LIGHT = {Priority.high: ("stop", "СТОП: не эксплуатировать до дефектовки"), Priority.medium: ("service", "Плановое ТО: в ближайшее время"), Priority.low: ("go", "Можно ехать, наблюдать")}

QA_SYSTEM = """Ты помощник механика. Отвечай ТОЛЬКО по переданным фрагментам руководства двигателя этой машины.
Каждое утверждение подкрепляй дословной цитатой (12-200 символов подряд) и меткой S#. Нет ответа во фрагментах — found=false, ничего не выдумывай.
Ответ коротко (до 6 предложений или пунктов), по-русски, для механика.
JSON: {"found":true|false,"answer":"текст","citations":[{"source":"S#","quote":"дословно"}]}"""


@dataclass
class Session:
    sid: str
    vehicle: Optional[Vehicle] = None
    check: Optional[CheckResult] = None
    decision: Optional[PolicyDecision] = None
    draft: Optional[TicketDraft] = None
    draft_id: Optional[str] = None
    history: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    last_fault_hits: list = field(default_factory=list)


class MechanicAgent:
    def __init__(self, fleet: Fleet, ml: MlClient, toir: ToirClient, index: ManualIndex, llm: Llm, trace_dir: str | Path | None = None):
        self.fleet, self.ml, self.toir, self.index, self.llm = fleet, ml, toir, index, llm
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.sessions: dict[str, Session] = {}

    # ---------- session helpers ----------
    def session(self, sid: str | None) -> Session:
        sid = sid or uuid.uuid4().hex[:10]
        return self.sessions.setdefault(sid, Session(sid))

    def _t(self, s: Session, kind: str, **data: Any) -> None:
        s.trace.append({"t": time.strftime("%H:%M:%S"), "kind": kind, **data})
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            (self.trace_dir / f"{s.sid}.json").write_text(json.dumps({"session": s.sid, "events": s.trace}, ensure_ascii=False, indent=1), encoding="utf-8")

    def select_vehicle(self, sid: str, vehicle_id: str) -> dict:
        s = self.session(sid)
        s.vehicle = self.fleet.get(vehicle_id)
        s.check = s.decision = s.draft = s.draft_id = None
        self._t(s, "get_vehicle", vehicle_id=vehicle_id, engine=s.vehicle.engine_type)
        checks = self.ml.list_checks(vehicle_id)
        self._t(s, "list_checks", vehicle_id=vehicle_id, count=len(checks))
        return {"sid": s.sid, "vehicle": s.vehicle.model_dump(), "checks": checks}

    # ---------- main scenario: get diagnostic result ----------
    def run_check(self, sid: str, session_id: str | None = None) -> dict:
        s = self.session(sid)
        if not s.vehicle:
            return self._reply(s, "Сначала выберите машину.")
        v = s.vehicle
        if not session_id:
            checks = self.ml.list_checks(v.vehicle_id)
            session_id = max(checks, key=lambda c: c["datetime"])["session_id"]  # latest by date
        check = self.ml.get_check(v.vehicle_id, session_id)
        if check.vehicle_id != v.vehicle_id:
            return self._reply(s, "Проверка принадлежит другой машине: отклонено.")
        s.check, s.draft, s.draft_id = check, None, None
        self._t(s, "get_check", session_id=session_id, status=check.status.value, quality=check.quality.value if check.quality else None, score=check.anomaly_score, confidence=check.confidence, fault=check.fault_code)

        if check.status == CheckStatus.check_failed or (check.quality and check.quality.value == "bad"):
            s.decision = policy.decide(check, found_specific=False, found_general_inspection=False)
            self._t(s, "policy", decision=s.decision.value, why="сбой записи или плохое качество: диагноз не ставим, заявку не создаём")
            return self._reply(s, "Запись непригодна для диагностики. Диагноз не ставлю и заявку не создаю. Запишите звук двигателя заново и запросите результат ещё раз.",
                               card={"kind": "failed", "check": self._check_view(check), "reason": "сбой записи" if check.status == CheckStatus.check_failed else "плохое качество записи"})
        if check.status == CheckStatus.normal:
            s.decision = policy.decide(check, found_specific=False, found_general_inspection=False)
            self._t(s, "policy", decision=s.decision.value, why="норма")
            return self._reply(s, "Двигатель в норме: отклонений по записи не найдено. Заявка в ТОиР не нужна.",
                               card={"kind": "normal", "check": self._check_view(check), "light": {"code": "go", "text": "Можно ехать"}})

        # anomaly: retrieve in THIS engine's manuals only
        query = f"{check.fault_title or ''} {check.symptoms or ''}"
        hits = self.index.search(v.engine_type, query, k=6)
        frags = make_fragments(hits)
        self._t(s, "search_manual", engine=v.engine_type, query=query[:200], hits=[h.as_dict() for h in hits])
        ex = Extraction()
        llm_used = False
        if frags:
            try:
                ex, raw = extract_with_llm(self.llm, check.fault_title or "", check.symptoms or "", frags)
                llm_used = True
                self._t(s, "llm_extract", coverage_raw=raw.get("coverage"), verified_works=len(ex.works), verified_parts=len(ex.parts), dropped=ex.dropped)
            except LlmUnavailable as e:
                ex = extract_fallback(check.fault_title or "", frags)
                self._t(s, "llm_fallback", reason=str(e), coverage=ex.coverage)
        dec = policy.decide(check, found_specific=ex.coverage == "specific", found_general_inspection=ex.coverage == "general")
        s.decision = dec
        prio, why = compute_priority(check)
        flags = doubt_flags(check)
        self._t(s, "policy", decision=dec.value, priority=prio.value, priority_basis=why, doubt=flags)
        card = self._anomaly_card(check, ex, prio, why, flags, dec)

        if not policy.may_post_ticket(dec):
            text = ("В руководстве вашего двигателя нет инструкции по этой неисправности и нет общей процедуры осмотра узла. "
                    "Рекомендую дефектовку. Заявку в ТОиР не формирую: нет опоры на документ.")
            card["kind"] = "no_basis"
            return self._reply(s, text, card=card)

        draft = self._build_draft(check, ex, prio)
        s.draft, s.draft_id = draft, hashlib.sha1(draft.model_dump_json().encode()).hexdigest()[:10]
        card["draft"] = self._draft_view(draft)
        card["draft_id"] = s.draft_id
        text = "Нашёл отклонение. Ниже диагноз, риск и состав заявки. Отправить в ТОиР? Нажмите «Подтвердить» или напишите «подтверждаю»."
        if dec == PolicyDecision.propose_ticket_general_inspection:
            text = ("Частной инструкции именно по этой неисправности в руководстве вашего двигателя нет. Есть общая процедура осмотра узла (ссылка ниже). "
                    "Рекомендую дефектовку; заявку на дефектовку по общей процедуре могу отправить после вашего подтверждения.")
        if flags:
            text += " Обратите внимание: " + "; ".join(flags) + "."
        return self._reply(s, text, card=card)

    def _build_draft(self, check: CheckResult, ex: Extraction, prio: Priority) -> TicketDraft:
        refs: list[SourcePage] = []
        for it in [*ex.works, *ex.parts]:
            if it.source and not any(r.document == it.source.document and r.page == it.source.page for r in refs):
                refs.append(SourcePage(document=it.source.document, page=it.source.page))
        diag = f"{check.fault_code or ''} {check.fault_title or ''}".strip()
        if check.symptoms:
            diag += f". Признаки: {check.symptoms}"
        return TicketDraft(vehicle_id=check.vehicle_id, session_id=check.session_id, fault_code=check.fault_code, diagnosis=diag,
                           priority=prio, works=ex.works, parts=ex.parts, source_pages=refs)

    # ---------- confirm gate ----------
    def confirm(self, sid: str, draft_id: str | None = None) -> dict:
        s = self.session(sid)
        if not s.draft or not s.decision or not s.vehicle:
            return self._reply(s, "Нет подготовленной заявки для отправки.")
        if draft_id and draft_id != s.draft_id:
            return self._reply(s, "Состав заявки изменился. Проверьте и подтвердите ещё раз.")
        if not policy.may_post_ticket(s.decision):
            self._t(s, "blocked", why="policy forbids ticket")
            return self._reply(s, "Политика не позволяет отправить заявку.")
        err = self.validate_draft(s.vehicle, s.draft)
        if err:
            self._t(s, "blocked", why=err)
            return self._reply(s, f"Заявку не отправляю: {err}")
        ack = self.toir.create_ticket(s.draft)
        self._t(s, "create_toir_ticket", ticket_id=ack.ticket_id, vehicle=s.draft.vehicle_id, session_id=s.draft.session_id)
        s.draft = s.draft_id = None
        return self._reply(s, f"Заявка отправлена в ТОиР: {ack.ticket_id}.", card={"kind": "ticket", "ticket_id": ack.ticket_id})

    def cancel(self, sid: str) -> dict:
        s = self.session(sid)
        s.draft = s.draft_id = None
        self._t(s, "cancel_ticket")
        return self._reply(s, "Хорошо, заявку не отправляю.")

    def validate_draft(self, vehicle: Vehicle, draft: TicketDraft) -> str | None:
        """Last line of defence before POST: engine, documents, pages and quotes must match the manuals."""
        if draft.vehicle_id != vehicle.vehicle_id:
            return "машина в заявке не совпадает с выбранной"
        if not draft.works or not draft.source_pages:
            return "нет работ или ссылок"
        for it in [*draft.works, *draft.parts]:
            if it.source is None:
                if it in draft.works:
                    return "работа без ссылки"
                continue
            page = next((p for p in self.index.pages if p.document == it.source.document and p.page == it.source.page and vehicle.engine_type in p.engine_types), None)
            if page is None:
                return f"ссылка «{it.source.document}», стр. {it.source.page} не относится к руководству этого двигателя"
            if it.source.quote and not quote_in_page(it.source.quote, page.text):
                return f"цитата не найдена на стр. {it.source.page}"
        return None

    # ---------- free chat ----------
    def chat(self, sid: str, text: str) -> dict:
        s = self.session(sid)
        self._t(s, "user", text=text[:500])
        if s.draft and CONFIRM_RE.match(text):
            return self.confirm(sid, s.draft_id)
        if s.draft and CANCEL_RE.match(text):
            return self.cancel(sid)
        if CONFIRM_RE.match(text) and not s.draft:
            return self._reply(s, "Сейчас нет заявки, ожидающей подтверждения.")
        if not s.vehicle:
            return self._reply(s, "Выберите машину сверху: ответы даю только по руководству её двигателя.")
        if CHECK_RE.search(text) and len(text) < 80:
            return self.run_check(sid)
        return self.answer(s, text)

    def answer(self, s: Session, question: str) -> dict:
        v = s.vehicle
        q = question
        if s.check and s.check.fault_title and len(question.split()) < 6:
            q = f"{question} {s.check.fault_title}"  # short follow-up keeps fault context
        hits = self.index.search(v.engine_type, q, k=5)
        frags = make_fragments(hits)
        self._t(s, "search_manual", engine=v.engine_type, query=q[:200], hits=[h.as_dict() for h in hits])
        none_msg = f"В руководстве двигателя {v.engine_type} ответа на этот вопрос не нашёл. Лучше уточнить у специалиста или провести дефектовку."
        if not frags:
            return self._reply(s, none_msg, card={"kind": "answer", "found": False})
        try:
            raw = self.llm.json("answer", QA_SYSTEM, f"Двигатель: {v.engine_type}\nВопрос: {question}\n\nФрагменты:\n\n" + "\n\n".join(f.prompt_text() for f in frags), max_tokens=2500)
        except LlmUnavailable as e:
            self._t(s, "llm_unavailable", reason=str(e))
            return self._reply(s, "Модель сейчас недоступна. Вот страницы руководства по запросу:", card={"kind": "answer", "found": True, "citations": [self._cit(f.page, None) for f in frags[:3]]})
        by = {f.label: f for f in frags}
        cits = []
        for c in raw.get("citations") or []:
            f = by.get(str(c.get("source")))
            if f and quote_in_page(str(c.get("quote", "")), f.page.text):
                cits.append(self._cit(f.page, c["quote"]))
        found = bool(raw.get("found")) and bool(cits) and bool(raw.get("answer"))
        self._t(s, "llm_answer", found_raw=raw.get("found"), verified_citations=len(cits))
        if not found:
            return self._reply(s, none_msg, card={"kind": "answer", "found": False})
        uniq = {(c["document"], c["page"]): c for c in cits}
        return self._reply(s, str(raw["answer"]).strip(), card={"kind": "answer", "found": True, "citations": list(uniq.values())})

    # ---------- views ----------
    @staticmethod
    def _cit(page, quote) -> dict:
        return {"document": page.document, "page": page.page, "quote": quote}

    @staticmethod
    def _check_view(c: CheckResult) -> dict:
        return {"session_id": c.session_id, "status": c.status.value, "quality": c.quality.value if c.quality else None, "confidence": c.confidence,
                "anomaly_score": c.anomaly_score, "fault": f"{c.fault_code or ''} {c.fault_title or ''}".strip(), "symptoms": c.symptoms, "model_version": c.model_version}

    def _anomaly_card(self, c, ex, prio, why, flags, dec) -> dict:
        code, label = LIGHT[prio]
        return {"kind": "anomaly", "check": self._check_view(c), "light": {"code": code, "text": label}, "priority": prio.value, "priority_basis": why, "doubt": flags,
                "risk": ex.risk or "Последствия в найденных фрагментах руководства не описаны.",
                "coverage": ex.coverage, "decision": dec.value,
                "sources": list({(w.source.document, w.source.page): {"document": w.source.document, "page": w.source.page, "quote": w.source.quote} for w in ex.works}.values())}

    @staticmethod
    def _draft_view(d: TicketDraft) -> dict:
        return {"vehicle_id": d.vehicle_id, "session_id": d.session_id, "fault_code": d.fault_code, "diagnosis": d.diagnosis, "priority": d.priority.value,
                "works": [{"title": w.title, "document": w.source.document, "page": w.source.page} for w in d.works],
                "parts": [{"title": p.title, "document": p.source.document if p.source else None, "page": p.source.page if p.source else None} for p in d.parts],
                "references": [{"document": r.document, "page": r.page} for r in d.source_pages]}

    def _reply(self, s: Session, text: str, card: dict | None = None) -> dict:
        s.history.append({"role": "assistant", "text": text})
        return {"sid": s.sid, "text": text, "card": card, "pending_draft_id": s.draft_id}
