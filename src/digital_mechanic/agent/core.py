"""Digital Mechanic agent: LLM tool-calling loop over separate tools (ML, RAG, TOiR, history,
repair), with deterministic guards around everything that has consequences.

* The LLM chooses tools by context (TZ F5); policy, urgency, ticket validation and the
  confirmation gate are plain code.
* Every technical claim must cite a fragment label; numbers are checked against the
  retrieved pages; tickets accept only operations backed by verbatim quotes.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .. import pii, policy
from . import answer_post, applicability, contradictions
from ..clients import Fleet, MlClient, ServiceError, ToirClient
from ..i18n import LANGS, answer_lang, lang_or_default
from ..retrieval.bm25 import ExtraIndex, ManualIndex, snippet
from ..schemas import CheckResult, CheckStatus, PolicyDecision, Reference, TicketRequest, Urgency, Vehicle
from ..store import Store
from ..history import msk_time, summary_for_agent as hist_summary, timeline as build_timeline
from .grounding import cited_labels, quote_in_text, unverified_numbers
from .llm import Llm, LlmUnavailable

# No model answer in time (free model queue at night, turn budget spent): the manual pages themselves, never invented text.
FALLBACK_HEAD = "Ассистент сейчас не успел ответить — вот что сказано в руководстве по вопросу:"
from .prompts import DETAIL, EXPAND_PROMPT, EXPAND_SYSTEM, EXPERIENCE, MANAGER_SYSTEM, MECHANIC_SYSTEM

URG_RU = {"high": "высокий", "medium": "средний", "low": "низкий"}
# Organizers 26.11: no «обратитесь к специалисту» (the mechanic IS the specialist) -> a concrete action.
NO_DATA_TEXT = ("В руководстве для этого двигателя данных нет. Что можно сделать: провести дефектовку узла по порядку автопарка "
                "или открыть ближайший раздел руководства («Руководства» в карточке машины); вопрос записан в журнал пробелов базы для главного механика.")


def work_of_quote(quote: str) -> str:
    """«Ранний впрыск топлива в цилиндры — Отрегулировать угол опережения» -> «Отрегулировать угол опережения»
    (fault-table row: the remedy after the dash); a plain sentence -> itself, capitalised, without the final dot."""
    q = re.sub(r"\s+", " ", quote or "").strip(" .;")
    parts = re.split(r"\s[—–]\s", q)
    w = parts[-1].strip() if len(parts) > 1 and len(parts[-1]) > 6 else q
    return (w[:1].upper() + w[1:])[:200]


# requests that are actions (ticket, repair, diagnosis, history) — no manual prefetch before the LLM call
PREFETCH_SKIP = re.compile(r"заявк|дефектовк|начать ремонт|начни ремонт|ремонт по шагам|истори|результат|получи\w* диагноз|главн\w* механик|"
                           r"сколько машин|подтвержда|отмен|^\s*(да|нет|ок|спасибо)[\s.!]*$", re.I)
TICKET_RE = re.compile(r"заявк|дефектовк|начать ремонт|начни ремонт|ремонт по шагам", re.I)
CONFIRM_RE = re.compile(r"^\s*(да[,.!\s]*)?(подтвержда\w*|отправля\w*|отправь\w*|tasdiqla\w*|тасдиқ\w*|растаймын|xác nhận|पुष्टि)", re.I)
# Organizers 27.11 (C1, MAZ M333YK47 / S-0005, F08 early injection): the mechanic checks the injection timing marks.
# «метки не совпадают» is enough for an adjustment ticket (п. 5.12.1 РК) after its own confirmation, no measured angle;
# «метки совпали» is saved, but the check is NOT closed as serviceable: F08 is the fuel delivery as a whole, early injection
# is only one cause of the knock -> next causes / checks from the manual, no ticket yet.
MARKS_NO_RE = re.compile(r"^\s*(механик:\s*)?метки\s+(не\s+совпа\w*|расходятся|не\s+сходятся)", re.I)
MARKS_YES_RE = re.compile(r"^\s*(механик:\s*)?метки\s+(совпа\w*|сошлись|сходятся)", re.I)
MARKS_CODES = {"F08"}
CLOSE_OK_RE = re.compile(r"(?i)(?<![нН]е\s)(?<![нН]е)\bисправ(ен|на|но|ны)\b|проверк\w*\s+(можно\s+)?(закры|заверш)\w*|можно\s+(выпуска\w*|эксплуатир\w*)|вернуть\s+на\s+линию")
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
    {"type": "function", "function": {"name": "prepare_ticket", "description": "Подготовить черновик заявки на дефектовку в ТОиР (только по просьбе механика). Каждая работа и запчасть — метка фрагмента S# и дословная цитата: строка таблицы «причина — способ устранения» или фраза с действием; формулировку работы система берёт из цитаты (text — только если цитата не называет действие). Приоритет считает система.",
     "parameters": {"type": "object", "properties": {
         "conclusion": {"type": "string", "description": "диагноз для заявки, одно предложение по-русски"},
         "operations": {"type": "array", "items": {"type": "object", "properties": {"source": {"type": "string"}, "quote": {"type": "string"}, "text": {"type": "string"}}, "required": ["source", "quote"]}},
         "spare_parts": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "source": {"type": "string"}, "quote": {"type": "string"}}, "required": ["source", "quote"]}}},
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


PROGRESS = {"search_manual": "🔎 Ищу в руководстве", "expand": "📖 Разворачиваю ответ по тем же страницам", "search_extra_docs": "📗 Доп. руководство", "get_diagnosis": "🎧 Запрашиваю ML-диагностику",
            "get_vehicle_history": "🗂 Смотрю историю машины", "prepare_ticket": "📝 Готовлю заявку", "start_repair": "🛠 Составляю план ремонта",
            "update_repair": "✔ Отмечаю шаг", "fleet_overview": "📊 Сводка по парку"}


_NODATA_SENT = re.compile(r"(?i)фрагмент[а-я]*\b[^.!?\n]{0,120}\b(нет|не привод|не указ|не найд|отсутств)|(нет|не привод|не указ|не найд)[^.!?\n]{0,80}\bфрагмент")


def drop_unasked_nodata(answer: str) -> str:
    """A grounded answer (2+ citations) that adds «числовых допусков в найденных фрагментах нет — в руководстве данных нет»:
    the claim is about the retrieved fragments, not the manual (ЯМЗ-236 РЭ p. 31 has the valve clearances; Vercel audit
    23:17, C1 explanation), and «фрагменты» is our jargon. Such a sentence goes; a refusal (no citations) stays as it is."""
    if len(re.findall(r"\[(?:S|X)\d+\]", answer)) < 2:
        return answer
    out = []
    for line in answer.split("\n"):
        sents = re.split(r"(?<=[.!?])\s+", line)
        keep = [x for x in sents if not _NODATA_SENT.search(x)]
        rest = " ".join(keep).strip()
        if keep != sents and not rest:
            continue
        out.append(rest if keep != sents else line)
    res = "\n".join(out).strip()
    return res if len(re.findall(r"\[(?:S|X)\d+\]", res)) >= 1 else answer


@dataclass
class Conv:
    cid: str
    uid: str
    vehicle: Optional[Vehicle] = None
    check: Optional[CheckResult] = None
    decision: Optional[PolicyDecision] = None
    draft: Optional[dict] = None
    lang: str = "ru"
    recalled: bool = False  # memory callback already used in this conversation
    pending: Optional[dict] = None  # manager briefing: proposed ticket waiting for a yes/no
    frags: dict[str, dict] = field(default_factory=dict)
    n_s: int = 0
    n_x: int = 0
    preface: str = ""
    contra: list = field(default_factory=list)  # known manual contradictions matched by the current question
    no_instruction: Optional[str] = None  # defect-check recommendation when the manual has no applicable repair
    hold_ticket: bool = False  # this turn: marks matched -> no ticket, the check stays open
    lock: threading.Lock = field(default_factory=threading.Lock)


def _media_query(note: str) -> str:
    """Search words from a photo/video/sound description: the visible signs, not the boilerplate."""
    t = re.sub(r"(?i)ГОСНОМЕР:.*|Это не результат ML-диагностики.*|Запись приложена.*|\d+([.,]\d+)?", " ", note or "")
    return " ".join(t.split()[:60])


@dataclass
class Turn:
    text: str = ""
    cards: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    tokens: int = 0
    ok: bool = True
    facts: list[dict] = field(default_factory=list)
    understood: list = field(default_factory=list)  # [(original, manual term)] "did you mean"
    actions: list[dict] = field(default_factory=list)  # contextual buttons under the answer (instead of offer-questions in the text)
    speak: str = ""  # what to say aloud when different from the text (manager briefing)

    def as_dict(self) -> dict:
        return {"text": self.text, "cards": self.cards, "sources": self.sources, "tools": self.tools, "facts": self.facts,
                "understood": self.understood, "actions": self.actions, "speak": self.speak}


ALT_PROMPT = ("Другие варианты: предложи до 3 других возможных причин, которые мы ещё НЕ обсуждали, и как проверить каждую — "
              "только по руководству этого двигателя (поищи в разделе неисправностей и методов их устранения), с метками источников. "
              "Если других причин в руководстве нет — так и скажи.")
HYPOTHESIS_PROMPT = ("Проверь гипотезу механика: «{h}». Найди в руководстве этого двигателя, что её подтверждает или опровергает и как её проверить "
                     "(шаги, нормативы — с метками). Если в руководстве этого нет — честно скажи.")


def _same(a: str, b: str) -> bool:
    import difflib

    return difflib.SequenceMatcher(None, a[:1500], b[:1500]).ratio() > 0.9


_ABSENT_RE = re.compile(r"(не|нигде не) (упомина\w*|указ\w*|привод\w*|опис\w*|рассматрива\w*|встреча\w*)|в (этом |данном )?разделе нет|отсутству\w* в разделе|нет (информации|сведений|данных)", re.I)


_TABLE_HEAD_RE = re.compile(r"^(?:Вероятн\S* |Возможн\S* )?(?:Причин|Неисправн)[^\n]{0,40} — (?:Способ|Метод)", re.M)


def _stems(x: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[а-яёa-z0-9]{4,}", (x or "").lower().replace("ё", "е"))}


def table_row_group(text: str, hint: str) -> tuple[str, list[str], int] | None:
    """Fault table page («причина — способ устранения», one row per line, symptom headings between): the symptom group
    that holds the cited row -> (heading, rows, index of the cited row). None if the page is no such table or nothing matches.
    The extract used to retell the neighbouring row of another symptom (p. 53: «пониженное давление масла» for the valve-clearance row)."""
    if not text or not _TABLE_HEAD_RE.search(text):
        return None
    # the hint is the answer text before the citation chip («…прогреть насос [S1]; отрегулировать тепловые зазоры клапанов [S2]»):
    # the clause right before the chip is what this page was cited for
    segs = [x for x in re.split(r"[;\n]|\(с\. ?\d+\)|\[[^\]]*\]", hint or "") if len(_stems(x)) >= 2]
    hs = _stems(segs[-1]) if segs else _stems(hint)
    if len(hs) < 2:
        return None
    groups: list[tuple[str, list[str]]] = [("", [])]
    for line in text.split("\n"):
        line = line.strip()
        if not line or _TABLE_HEAD_RE.match(line) or re.fullmatch(r"\d{1,4}", line) or line.startswith("*"):
            continue
        if " — " in line:
            groups[-1][1].append(line)
        elif groups[-1][1] or not groups[-1][0]:
            groups.append((line, []))
        else:  # heading wrapped onto a second line
            groups[-1] = (groups[-1][0] + " " + line, [])
    best, where = 0.0, None
    for gi, (head, rows) in enumerate(groups):
        cands = [(-1, head)] + list(enumerate(rows))  # -1: the hint names the symptom heading itself (nothing in bold)
        for ri, r in cands:
            sc = len(hs & _stems(r)) / len(hs) if r else 0.0
            if sc > best + 1e-9:
                best, where = sc, (gi, ri)
    if not where or best < 0.5 or not groups[where[0]][1]:
        return None
    head, rows = groups[where[0]]
    hit = where[1]
    return head.rstrip("*").strip(), rows, hit


def _rows_word(n: int) -> str:
    return "причина" if n % 10 == 1 and n % 100 != 11 else "причины" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "причин"


def table_headings(text: str) -> list[tuple[str, int]]:
    """Symptom headings of a fault-table page with their row counts (rows before the first heading: «продолжение»)."""
    out: list[list] = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line or _TABLE_HEAD_RE.match(line) or re.fullmatch(r"\d{1,4}", line) or line.startswith("*"):
            continue
        if " — " in line:
            if not out:
                out.append(["Продолжение с предыдущей страницы", 0])
            out[-1][1] += 1
        elif out and out[-1][1] == 0 and not out[-1][0].startswith("Продолжение"):
            out[-1][0] += " " + line
        else:
            out.append([line, 0])
    return [(h.rstrip("*").strip(), n) for h, n in out if n]


_LEADIN_RE = re.compile(r"^\s*(?:(?:На|Согласно)\s+(?:стр(?:анице|\.)?\s*\d+|(?:этой\s+)?странице|таблице|разделу?|руководству)"
                        r"(?:\s+(?:руководства|указано|сказано|говорится|приведено|описано))?\s*[,:]?\s*(?:что\s+)?)", re.I)


def _drop_leadin(text: str) -> str:
    """«На стр. 268 указано, что не являются браковочными…» -> «Не являются браковочными…» (the chip already names the page;
    the prompt forbids the lead-in, the free model sometimes writes it anyway — Vercel 27.11 02:20, desktop)."""
    m = _LEADIN_RE.match(text or "")
    if not m or len(text) - m.end() < 20:
        return text
    rest = text[m.end():]
    return rest[:1].upper() + rest[1:]


def _cap_extract(text: str, max_sent: int = 4, max_items: int = 5, max_chars: int = 650) -> str:
    """The extract is a glance before the full section: 2–4 sentences or 3–5 points (the prompt asks, the free model sometimes
    writes 7 sentences / 9 points — Vercel 27.11). Cut at sentence / line boundaries, never mid-sentence."""
    t = (text or "").strip()
    lines = [l for l in t.split("\n") if l.strip()]
    items = [l for l in lines if re.match(r"\s*(?:[-*•]|\d+[.)])\s", l)]
    if len(items) >= 2:  # a list: the lead line(s) + the first points
        out, n = [], 0
        for l in lines:
            is_item = l in items
            if is_item:
                n += 1
                if n > max_items:
                    break
            elif n and not is_item:
                break  # a paragraph after the list: other topic
            out.append(l)
        return "\n".join(out)
    parts = re.split(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\d])", " ".join(lines))
    out = parts[:max_sent]
    while len(out) > 1 and len(" ".join(out)) > max_chars:  # one long sentence stays whole
        out.pop()
    return " ".join(out) if len(out) < len(parts) else t


def _drop_absence_claims(text: str) -> str:
    """The extract must not say what the section lacks («код F08 не упоминается»): drop such sentences, keep the rest
    (a bullet list keeps its lines)."""
    if not text or not _ABSENT_RE.search(text):
        return text
    lines = []
    for line in text.split("\n"):
        parts = re.split(r"(?<=[.!?])\s+", line)
        kept = [x for x in parts if not _ABSENT_RE.search(x)]
        if kept or not line.strip():
            lines.append(" ".join(kept))
    out = "\n".join(lines).strip()
    return out or text


class Agent:
    def __init__(self, fleet: Fleet, ml: MlClient, toir: ToirClient, index: ManualIndex, extra: ExtraIndex | None,
                 llm: Llm, store: Store, engine_docs: dict, max_rounds: int = 5, pii_masking: bool = True):
        self.fleet, self.ml, self.toir, self.index, self.extra = fleet, ml, toir, index, extra
        self.llm, self.store, self.docs, self.max_rounds, self.pii_masking = llm, store, engine_docs, max_rounds, pii_masking
        self.convs: dict[str, Conv] = {}
        self.progress: dict[str, list[str]] = {}
        self.drafts: dict[str, str] = {}  # live verified lines of the answer being written (see _live)
        self._lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._sections = None
        self._extracts: dict[tuple, dict] = {}
        # «Подробнее» is written in the background right after a brief answer (the mechanic reads the brief one
        # meanwhile), so the click returns the ready text or joins the run in progress. Off on serverless (Vercel):
        # threads freeze after the response there.
        import os

        self.expand_prefetch = os.getenv("EXPAND_PREFETCH", "0" if os.getenv("VERCEL") else "1") == "1"
        self._prefetch: dict[str, dict] = {}
        # manual questions: facts once (persona-independent, cached), then a style-only pass (agent/grounded.py)
        from .grounded import FactPipeline

        self.facts_pass = os.getenv("FACTS_PASS", "1") == "1"
        self.fp = FactPipeline(self)

    @property
    def normalizer(self):
        if getattr(self, "_norm", None) is None:
            from ..retrieval.synonyms import Normalizer

            with self._init_lock:  # built once on the first request; parallel first requests wait for it
                if getattr(self, "_norm", None) is None:
                    self._norm = Normalizer(self.index.pages)
        return self._norm

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
        pg = self.index.get_page(doc_id, page) if hasattr(self.index, "get_page") else None
        grp = table_row_group(getattr(pg, "text", "") or "", hint)
        if grp:  # fault table: only the cited row's symptom group, never the neighbouring symptom
            head, rows, i = grp
            if lang.startswith("русск"):
                lines = [f"**{head}** (стр. {page}):" if head else f"Таблица неисправностей, стр. {page}:"]
                lines += [("- **" + r.replace(" — ", "** — ", 1)) if k == i else "- " + r for k, r in enumerate(rows)]
                out = {"text": "\n".join(lines), "ok": True, "dropped_numbers": [], "table_row": True}
                self._extracts[key] = out
                return out
            body = (head + "\n" if head else "") + "\n".join(rows)
        elif pg is not None and _TABLE_HEAD_RE.search(getattr(pg, "text", "") or ""):
            # fault table, no row matches the cited clause: list what this page covers (the LLM answered the question from the
            # neighbouring page: «F08 означает подсос воздуха…» on p. 53, which is about knocking and oil pressure)
            heads = table_headings(pg.text)
            if lang.startswith("русск") and heads:
                out = {"text": f"Таблица неисправностей, стр. {page}. На этой странице:\n" + "\n".join(f"- **{h}** — {n} {_rows_word(n)}" for h, n in heads),
                       "ok": True, "dropped_numbers": [], "table_row": False}
                self._extracts[key] = out
                return out
            body = pg.text
        pk = hashlib.sha1(json.dumps([*key, body], ensure_ascii=False).encode()).hexdigest()  # survives a restart; a re-index changes body
        try:
            self.store.x("CREATE TABLE IF NOT EXISTS extract_cache(k TEXT PRIMARY KEY, out TEXT, ts REAL)")
            row = self.store.one("SELECT out FROM extract_cache WHERE k=?", (pk,))
            if row:
                out = json.loads(row["out"])
                out["text"] = _cap_extract(out.get("text") or "")
                self._extracts[key] = out
                return out
        except Exception:
            pass
        try:
            msg, _ = self.llm.chat("section_extract", [
                {"role": "system", "content": "Ты помогаешь механику. Перескажи раздел руководства коротко (2–4 предложения или 3–5 пунктов) "
                 f"на {lang} языке, только по тексту раздела, ничего не добавляя. Если задан вопрос — выжимка отвечает на него только тем, что есть в разделе; если в разделе ответа нет — перескажи, о чём раздел, не отвечай от себя. "
                 "Начни с того, что сказано в строке цитаты и рядом с ней на стр. " + str(page) + ", затем — только то из раздела, что "
                 "относится к этому же вопросу; другие темы раздела не пересказывай. "
                 "Никогда не пиши, что чего-то (например, кода ошибки) в разделе нет или оно не упоминается: перескажи то, что в разделе "
                 "относится к теме вопроса и цитате (причины, проверки, способы устранения). "
                 "Числа, моменты, зазоры, давления переписывай ТОЛЬКО если они есть в тексте, без изменений. Без вступлений: не начинай "
                 "со слов «На стр. N…» или «В таблице указано», сразу по делу."},
                {"role": "user", "content": f"Вопрос механика: {question or '-'}\nЦитата: {hint[:300]}\nРаздел «{sec.label}» (стр. {sec.span}):\n{body}"}],
                max_tokens=500)
            text = (msg.content or "").strip()
        except Exception as e:  # LLM down -> UI still shows the full section
            return {"text": "", "ok": False, "error": type(e).__name__}
        bad = unverified_numbers(text, [body], allowed=set(), free_max=5) if text else []
        if bad:  # drop sentences carrying numbers not found in the section
            parts = re.split(r"(?<=[.!?])\s+|\n", text)
            text = " ".join(x for x in parts if not any(b in x or b.replace(".", ",") in x for b in bad)).strip()
        text = _cap_extract(_drop_leadin(_drop_absence_claims(text)))
        out = {"text": text, "ok": bool(text), "dropped_numbers": bad}
        self._extracts[key] = out
        if text:
            try:
                self.store.x("INSERT OR REPLACE INTO extract_cache(k, out, ts) VALUES (?,?,?)", (pk, json.dumps(out, ensure_ascii=False), time.time()))
            except Exception:
                pass
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
            bool(self.store.tickets(c.vehicle.vehicle_id)), user.get("experience") or "experienced",
            ticket_allowed=policy.may_create_ticket(c.check, c.vehicle) if c.check else True,
            blocked=c.decision.value if c.decision in (PolicyDecision.diagnosis_mismatch, PolicyDecision.recheck_low_confidence) else None)]
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
        c.decision = policy.decide(check, c.vehicle)
        blocked = c.decision in (PolicyDecision.diagnosis_mismatch, PolicyDecision.recheck_low_confidence)
        u, why = policy.urgency(check) if check.status == CheckStatus.anomaly and not blocked else (None, [])
        self.store.add_diagnosis(c.uid, check.model_dump(mode="json"), c.decision.value, u.value if u else None)
        return self.verdict_card(check, c.decision, u, why, c.vehicle)

    def restore_check(self, c: Conv, session_id: str | None) -> bool:
        """Another instance ran /api/diagnose (Vercel: requests of one dialog land on different instances, the
        conversation there has no check): re-read the same ML result so «explain», the ticket and the repair see it.
        No diagnosis record is added (it was stored when the check was run)."""
        if not c.vehicle or (c.check is not None and (not session_id or c.check.session_id == session_id)):
            return False
        if c.check is not None:  # a newer check on this instance wins over what the client remembers
            return False
        try:
            sid = session_id or self.fleet.latest_session_id(c.vehicle.vehicle_id)
            check = self.ml.diagnose(c.vehicle.vehicle_id, sid)
        except Exception:
            return False
        if check.vehicle_id != c.vehicle.vehicle_id:
            return False
        for s in self.fleet.sessions(c.vehicle.vehicle_id):
            if s.session_id == sid:
                check.checked_at = s.checked_at
        c.check = check
        c.decision = policy.decide(check, c.vehicle)
        return True

    def verdict_card(self, check: CheckResult, decision: PolicyDecision, u: Urgency | None, why: list[str], vehicle=None) -> dict:
        card = {"kind": "verdict", "status": check.status.value, "decision": decision.value, "check": check.model_dump(mode="json"),
                "doubt": policy.doubt_flags(check) if check.status == CheckStatus.anomaly else [],
                "thresholds": {"rerecord": policy.RERECORD_CONFIDENCE, "inspect": policy.LOW_CONFIDENCE},
                "ticket_allowed": policy.may_create_ticket(check, vehicle)}
        if decision == PolicyDecision.diagnosis_mismatch:
            card.update(light="repeat", light_text=policy.mismatch(check, vehicle))
        elif decision == PolicyDecision.recheck_low_confidence:
            card.update(light="repeat", light_text=(f"Уверенность {policy._ru(check.confidence)} ниже порога {policy._ru(policy.RERECORD_CONFIDENCE)}: "
                                                    "повторите запись. Заявка по этой проверке не оформляется."))
        elif u:
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
            mm = policy.mismatch(ch, v)
            if mm:
                lines.append("РАСХОЖДЕНИЕ С КАРТОЧКОЙ: " + mm + " Не объясняй этот тип неисправности, не ищи и не приводи нормативы и процедуры "
                             "другого двигателя, не подменяй его похожим типом. Скажи о расхождении и рекомендуй повторить проверку или дефектовку.")
            elif ch.status == CheckStatus.anomaly and ch.confidence is not None and ch.confidence < policy.RERECORD_CONFIDENCE:
                lines.append(f"НИЗКАЯ УВЕРЕННОСТЬ {policy._ru(ch.confidence)} < порога {policy._ru(policy.RERECORD_CONFIDENCE)}: рекомендуй повторить запись; "
                             "заявку не оформлять. Можно назвать, что проверить по руководству, с пометкой «Гипотеза»; не пиши «выводов не делаю» и «чем грозит» — "
                             "вывода нет, есть предположение до повторной записи.")
            elif ch.status == CheckStatus.anomaly:
                u, why = policy.urgency(ch)
                lines.append(f"Приоритет (рассчитан системой): {u.value} — {'; '.join(why)}.")
                flags = policy.doubt_flags(ch)
                if flags:
                    lines.append("СОМНЕНИЯ: " + "; ".join(flags) + ". Предупреди механика. Сначала осмотр узла на месте по руководству (заявку на осмотр не оформлять); "
                                 "заявка на ремонт — только если механик сознательно подтвердит неисправность после осмотра. В объяснении не пиши, что заявку можно оформить или что черновик готов; "
                                 "если механик сам просит заявку — вызови prepare_ticket, карточка попросит подтвердить предупреждение.")
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
                lines.append("Заявки в ТОиР: " + "; ".join(f"{t['ticket_id']} {t['anomaly_type']} {t['urgency']} {msk_time(t['ts'])}" for t in tks[:5]))
            r = self.store.repair_open(v.vehicle_id)
            if r:
                cur = r["steps"][r["current"]] if r["current"] < len(r["steps"]) else None
                who = (self.store.user(r["user_id"]) or {}).get("name") or "другой механик"
                lines.append(f"ИДЁТ РЕМОНТ «{r['title']}»: шаг {r['current'] + 1} из {len(r['steps'])}" + (f" — {cur['text']} ({cur['document']}, стр. {cur['page']})" if cur else "")
                             + f"; начал {who}, последняя отметка {datetime.fromtimestamp(r.get('updated') or r['started']).strftime('%d.%m %H:%M')}. "
                             "Если механик вернулся к машине — скажи, на каком шаге остановились и кто делал.")
        if c.contra:
            lines.append(contradictions.context_block(c.contra))
        if c.draft:
            lines.append(f"Черновик заявки ждёт подтверждения механика (приоритет {URG_RU.get(c.draft['request']['urgency'], c.draft['request']['urgency'])}).")
        if c.frags:
            lines.append("Уже найденные фрагменты: " + ", ".join(f"{k}={f['document']} стр.{f['page']}" for k, f in list(c.frags.items())[-12:]))
        return "\n".join(lines)

    def _system(self, c: Conv, user: dict, manager: bool = False, detail: str | None = None) -> str:
        lang = LANGS[answer_lang(user)]["llm"]
        personal = "".join("\n" + user[k] for k in ("_style", "_memory") if user.get(k))  # agent/style.py, agent/memory.py
        if manager:
            return MANAGER_SYSTEM.format(lang_name=lang, context=f"Сейчас {msk_time()}.") + personal
        rule = EXPERIENCE.get(user.get("experience") or "experienced", EXPERIENCE["experienced"])
        if user.get("_vocab_hint"):
            rule += "\n" + user["_vocab_hint"]  # adaptive profile: the mechanic's own terms (agent/profile.py)
        base = MECHANIC_SYSTEM.format(lang_name=lang, experience_rule=rule,
                                      length_rule=DETAIL.get(detail or user.get("detail") or "brief", DETAIL["brief"]), context=self._context(c))
        if personal:  # last in the prompt: the manner applies to every format above (also «Причина/Проверить/Исправить»)
            base += ("\n\nКАК ГОВОРИТЬ С ЭТИМ ЧЕЛОВЕКОМ (обязательно для любого формата ответа, включая краткий; "
                     "факты, цифры и метки источников не меняются):" + personal)
        return base

    def rehydrate_frags(self, c: Conv, sources: list[dict]) -> None:
        """Serverless (Vercel): «Подробнее» may land on another instance that never saw this conversation. The client
        sends back the answer's sources (label, doc_id, page); the page text is re-read from OUR index (never from the
        client), so the expand uses the same pages and labels as the brief answer."""
        for s in sources[:12]:
            try:
                label, doc_id, page = str(s.get("label") or ""), str(s.get("doc_id") or ""), int(s.get("page") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            m = re.fullmatch(r"([SX])(\d{1,3})", label)
            if not m or not doc_id or not page:
                continue
            old = c.frags.get(label)
            if old and old.get("doc_id") == doc_id and old.get("page") == page:
                continue
            if m.group(1) == "S":
                p = self.index.get_page(doc_id, page)
                if p is None:
                    continue
                c.frags[label] = dict(doc_id=p.doc_id, document=p.document, page=p.page, text=p.text, extra=False)
                c.n_s = max(c.n_s, int(m.group(2)))
            else:
                p = self.extra.get_page(doc_id, page) if self.extra else None
                if p is None:
                    continue
                c.frags[label] = dict(doc_id=p.doc_id, document=p.document, page=p.page, text=p.text, extra=True, image=p.image)
                c.n_x = max(c.n_x, int(m.group(2)))

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
        self.progress.setdefault(c.cid, []).append(PROGRESS["expand"])
        pf = self._prefetch_take(c, answer, labels)
        if pf is not None:
            # join the background run: mirror its live lines into this conversation's draft until it is done
            while not pf["done"].wait(0.25):
                self.drafts[c.cid] = pf["draft"]
            if pf.get("ok"):
                turn.text, turn.tokens = pf["text"], pf["tokens"]
                turn.tools.extend(pf["tools"] + [{"tool": "expand_prefetch", "ready_s": round(pf["t_done"] - pf["t0"], 1)}])
                turn.sources = [self._source_view(l, c) for l in cited_labels(turn.text) if l in c.frags]
                return turn
        return self._expand_run(c, user, answer, turn)

    def _prefetch_take(self, c: Conv, answer: str, labels: list[str]) -> dict | None:
        with self._lock:
            pf = self._prefetch.pop(c.cid, None)
        if not pf or pf["labels"] != labels:
            return None
        a, b = set(re.findall(r"\w+", pf["answer"].lower())), set(re.findall(r"\w+", answer.lower()))
        return pf if a and len(a & b) / len(a | b) >= 0.8 else None  # same answer (novice glosses may add words)

    def prefetch_expand(self, c: Conv, user: dict, answer: str) -> None:
        labels = [l for l in cited_labels(answer) if l in c.frags]
        if not labels or not self.llm.available:
            return
        snap = Conv(cid=c.cid, uid=c.uid, vehicle=c.vehicle, check=c.check, decision=c.decision, draft=c.draft,
                    frags={k: dict(v) for k, v in c.frags.items()}, n_s=c.n_s, n_x=c.n_x)
        pf = {"labels": labels, "answer": answer, "draft": "", "done": threading.Event(), "t0": time.time()}
        with self._lock:
            self._prefetch[c.cid] = pf

        def run() -> None:
            t = Turn()
            try:
                self._expand_run(snap, user, answer, t, sink=pf)
                pf.update(ok=t.ok and bool(t.text), text=t.text, tokens=t.tokens, tools=t.tools)
            except Exception as e:  # never break the conversation: the click falls back to a normal run
                pf.update(ok=False, error=str(e)[:200])
            finally:
                pf["t_done"] = time.time()
                pf["done"].set()

        threading.Thread(target=run, daemon=True, name=f"expand-{c.cid}").start()

    def _expand_run(self, c: Conv, user: dict, answer: str, turn: "Turn", sink: dict | None = None) -> "Turn":
        labels = [l for l in cited_labels(answer) if l in c.frags]
        # Reuse the fragments already retrieved for this answer (no new search) and a slim prompt: only the
        # grounding rules, no tool list / vehicle history -> fewer input tokens and a bounded output length.
        frags = "\n\n".join(f"[{l}] {c.frags[l]['document']}, стр. {c.frags[l]['page']}\n{snippet(c.frags[l]['text'], answer, 1800)}" for l in labels[:6])
        lang = LANGS[answer_lang(user)]["llm"]
        system = EXPAND_SYSTEM.format(lang_name=lang, experience_rule=EXPERIENCE.get(user.get("experience") or "experienced", EXPERIENCE["experienced"]))
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": EXPAND_PROMPT.format(answer=answer[:2500], frags=frags)}]
        try:
            msg, tok = self.llm.chat("expand", messages, max_tokens=1500, on_text=self._live(c, messages, sink))
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

    def tool_search_manual(self, c: Conv, query: str, k: int = 5) -> dict:
        from ..retrieval.expand import expand, make_boost

        query = self.normalizer.normalize(query)[0]
        queries, symptom = expand(query)
        hits = self.index.search_multi(c.vehicle.engine_model, queries, k=k, boost=make_boost(queries) if symptom else None,
                                       lang=getattr(c, "lang", None))
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

    def extra_hits(self, vehicle, query: str, k: int = 3) -> list:
        """Vehicle-manual pages for this truck: its own model's manual only when there is one (no sibling numbers)."""
        make = (vehicle.make or "").upper()
        if not self.extra or make not in self.extra.makes():
            return []
        model = (vehicle.model or "").lower()
        own = lambda p: bool(model) and any(model == m.lower() or model.startswith(m.lower() + "-") for m in p.models)  # noqa: E731
        hits = self.extra.search(make, query, k=24)
        if any(own(p) for p in self.extra.pages if p.make == make):
            hits = [h for h in hits if own(h[0])]
        return sorted(hits, key=lambda h: -h[1] * (1.6 if own(h[0]) else 1.0))[:k]

    def tool_search_extra(self, c: Conv, query: str, k: int = 3) -> dict:
        make = (c.vehicle.make or "").upper()
        if not self.extra or make not in self.extra.makes():
            return {"fragments": [], "note": f"дополнительных руководств для марки {c.vehicle.make} нет"}
        out = []
        hits = self.extra_hits(c.vehicle, query, k=k)
        for p, _ in hits:
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
                             "at": msk_time(t["ts"])} for t in self.store.tickets(vehicle_id)],
                "repairs": [{"title": r["title"], "status": r["status"], "step": r["current"] + 1, "steps": len(r["steps"])} for r in self.store.q(
                    "SELECT title,status,current,steps FROM repairs WHERE vehicle_id=? ORDER BY id DESC LIMIT 5", (vehicle_id,)) for r in [{**r, "steps": json.loads(r["steps"])}]]}

    def _verify_items(self, c: Conv, items: list[dict], kind: str) -> tuple[list[dict], list[str]]:
        ok, errs = [], []
        for it in items or []:
            lab, quote, text = str(it.get("source", "")).strip("[] "), str(it.get("quote", "")), str(it.get("text", "")).strip()
            text = text or work_of_quote(quote)  # the draft asks for quotes only (half the tokens): the work is the remedy part
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
        why_not = policy.ticket_refusal(ch, c.vehicle)
        if why_not:
            return {"error": f"Заявку создать нельзя: {why_not}."}
        if c.hold_ticket:
            return {"error": "Заявку сейчас не оформляем: метки угла опережения совпали, ранний впрыск не подтверждён. Проверка остаётся открытой — "
                             "назови механику следующие причины и проверки по руководству."}
        marks = self.marks_state(c)
        ops, e1 = self._verify_items(c, args.get("operations") or [], "работа")
        parts, e2 = self._verify_items(c, args.get("spare_parts") or [], "запчасть")
        ops, e3 = applicability.repair_ops(ops, ch.anomaly_type, c.frags)
        if not ops:
            rec = applicability.defect_recommendation(ch, c.vehicle)
            c.no_instruction = rec
            return {"error": "Заявку создать нельзя: в руководстве на двигатель этой машины нет применимой инструкции по ремонту найденной неисправности "
                             "(осмотр/проверку в заявку не включают — их делают на месте). Покажи механику причину и текст рекомендации на дефектовку.",
                    "rejected": e1 + e2 + e3, "defect_recommendation": rec}
        u, why = policy.urgency(ch)
        ban = policy.prohibition({it["label"]: c.frags[it["label"]] for it in ops if it.get("label") in c.frags})
        refs, seen = [], set()
        for it in ops + parts:
            key = (it["document"], it["page"])
            if key not in seen:
                seen.add(key); refs.append(Reference(manual=it["document"], pdf_page=it["page"]))
        ev = applicability.oil_pressure_evidence(c.frags) if ch.anomaly_type == "F02" else None
        if ev and (ev["document"], ev["page"]) not in seen:  # the manometer check page is the evidence of the conclusion
            refs.append(Reference(manual=ev["document"], pdf_page=ev["page"]))
        conclusion = (args.get("conclusion") or "").strip() or f"{ch.anomaly_title}: {ch.description}"
        req = TicketRequest(vehicle_id=ch.vehicle_id, session_id=ch.session_id, anomaly_type=ch.anomaly_type, conclusion=pii.mask(conclusion),
                            urgency=u, operations=[o["text"] for o in ops], spare_parts=[p["text"] for p in parts], references=refs)
        c.draft = {"id": uuid.uuid4().hex[:8], "request": req.model_dump(mode="json"), "operations": ops, "spare_parts": parts, "urgency_basis": why,
                   "requires_inspection": bool(policy.doubt_flags(ch)) and marks != "mismatch", "rejected": e1 + e2 + e3,
                   "inspection": "осмотр: метки угла опережения впрыскивания не совпадают (отметка механика)" if marks == "mismatch" else None,
                   "prohibition": ban, "urgency_options": policy.URGENCY_MEANING_FULL}
        return {"draft_ready": True, "urgency": u.value, "urgency_ru": URG_RU.get(u.value, u.value), "operations": len(ops), "spare_parts": len(parts), "rejected": e1 + e2,
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
        waiting = {t["vehicle_id"] for t in tickets if (t.get("status") or "created") == "created"} | {h["vehicle_id"] for h in self.store.handoffs("open") if h.get("vehicle_id")}
        ops = {"in_repair": 0, "awaiting": 0, "on_line": 0, "idle": 0}
        now = time.time()
        for v in self.fleet.all():
            ss = self.fleet.sessions(v.vehicle_id)
            last = ss[-1] if ss else None
            st = policy.fleet_status(last)
            counts[st] += 1
            u, why = policy.urgency(last) if last and last.status == CheckStatus.anomaly else (None, [])
            scores = [s.anomaly_score for s in ss if s.anomaly_score is not None]
            rank = {"quarantine": 0, "attention": 1, "ready": 2}[st]
            # item 21: operational state — in repair / idle (not allowed out, no repair started) / on the line; "awaiting" overlaps
            rep, idle = v.vehicle_id in open_rep, st == "quarantine" and v.vehicle_id not in open_rep
            ops["in_repair" if rep else "idle" if idle else "on_line"] += 1
            ops["awaiting"] += v.vehicle_id in waiting
            since = None
            if idle and last and last.checked_at:
                try:
                    since = max(0.0, round((now - datetime.fromisoformat(last.checked_at).timestamp()) / 86400, 1))
                except ValueError:
                    since = None
            rows.append({"ops": "in_repair" if rep else "idle" if idle else "on_line", "awaiting": v.vehicle_id in waiting, "idle_days": since,
                         "vehicle_id": v.vehicle_id, "garage_no": v.garage_no, "model": v.model or v.make, "engine_model": v.engine_model, "status": st,
                         "last": last.model_dump(mode="json") if last else None, "urgency": u.value if u else None, "urgency_basis": why,
                         "scores": scores, "in_repair": v.vehicle_id in open_rep, "tickets": len(tk_by_v.get(v.vehicle_id, [])),
                         "_rank": (rank, -(last.anomaly_score or 0) if last else 0)})
        rows.sort(key=lambda r: r.pop("_rank"))
        return {"counts": {**counts, "in_repair": len(open_rep), "tickets": len(tickets), "total": len(rows)}, "ops": ops, "vehicles": rows}

    # ------------------------------------------------------------------ chat turn
    def handle(self, c: Conv, user: dict, text: str, image_note: str | None = None, event: str | None = None,
               sources: list[dict] | None = None, session_id: str | None = None, detached: bool = False) -> Turn:
        from .llm import REQUEST_DEADLINE

        if detached and event == "expand":
            # «Подробнее» written ahead of the click (client prefetch): on a copy of the conversation, so the mechanic's next
            # question is not held by this conversation's lock and its live lines do not mix with this run's
            snap = Conv(cid=c.cid + "#pf", uid=c.uid, vehicle=c.vehicle, check=c.check, decision=c.decision, draft=None,
                        frags={k: dict(v) for k, v in c.frags.items()}, n_s=c.n_s, n_x=c.n_x, lang=c.lang)
            with self._lock:  # join the server-side prefetch of the same answer, if any
                pf = self._prefetch.pop(c.cid, None)
                if pf is not None:
                    self._prefetch[snap.cid] = pf
            try:
                return self.handle(snap, user, text, image_note, event, sources, session_id)
            finally:
                self.progress.pop(snap.cid, None); self.drafts.pop(snap.cid, None)

        budget = getattr(getattr(self.llm, "s", None), "llm_turn_budget_s", 0) or 0
        tok = REQUEST_DEADLINE.set(time.time() + budget) if budget > 0 and REQUEST_DEADLINE.get() is None else None
        try:
            return self._handle_locked(c, user, text, image_note, event, sources, session_id)
        finally:
            if tok is not None:
                REQUEST_DEADLINE.reset(tok)

    def _handle_locked(self, c: Conv, user: dict, text: str, image_note: str | None, event: str | None,
                       sources: list[dict] | None, session_id: str | None) -> Turn:
        with c.lock:
            c.lang = answer_lang(user)  # translated manual index (if imported) is searched in this language too
            if c.check is None and (session_id or event == "explain_diagnosis"):
                self.restore_check(c, session_id)
            if event == "expand" and sources:
                self.rehydrate_frags(c, sources)
            c.contra = contradictions.find(c.vehicle.engine_model if c.vehicle else None, text) if event != "expand" else []
            turn = self._handle(c, user, text, image_note, event)
            for k in c.contra:
                if not any(cd.get("kind") == "contradiction" for cd in turn.cards):
                    turn.cards.append(contradictions.card(k))
            if c.hold_ticket and turn.text:  # marks matched: never «исправно» / «проверку можно закрыть» (organizers 27.11)
                kept = [ln for ln in turn.text.split("\n") if not CLOSE_OK_RE.search(ln)]
                if len(kept) != len(turn.text.split("\n")):
                    turn.tools.append({"tool": "marks_guard", "removed": True})
                turn.text = "\n".join(kept).strip()
                turn.cards = [cd for cd in turn.cards if cd.get("kind") != "ticket_draft"]
                c.draft = None
            if c.preface:  # deterministic part (e.g. triage order by the TZ urgency rules), not subject to LLM grounding
                turn.text = c.preface + ("\n\n" + turn.text if turn.text else "")
                c.preface = ""
            if turn.text:
                turn.text, intents = answer_post.clean(turn.text)
                turn.text = answer_post.no_specialist(turn.text)
                if not turn.text.startswith(FALLBACK_HEAD):
                    turn.text, removed = answer_post.guard_uncited(turn.text)
                    if removed:
                        turn.tools.append({"tool": "uncited_guard", "removed": [r[:160] for r in removed]})
                held = self._ticket_held(c)
                if held and not any(cd.get("kind") == "ticket_draft" for cd in turn.cards):
                    turn.text, hit = answer_post.ticket_claims(turn.text, held)
                    if hit:
                        turn.tools.append({"tool": "ticket_claim_guard", "ok": True})
                self._log_gap(c, user, text, turn)
                labels = set(cited_labels(turn.text))
                turn.sources = [s for s in turn.sources if s["label"] in labels]
                if c.vehicle:
                    turn.actions = self.answer_actions(c, turn, intents)
            if turn.text and turn.sources and not turn.text.startswith(FALLBACK_HEAD):
                from .facts import key_facts

                frags = [{"label": l, **c.frags[l]} for l in cited_labels(turn.text) if l in c.frags and not c.frags[l].get("extra")]
                turn.facts = key_facts(frags, text, turn.text)
                # the UI offers «Подробнее» under a cited brief answer: start writing it now
                if (self.expand_prefetch and event != "expand" and turn.ok and c.vehicle and user.get("role") != "manager"
                        and (user.get("detail") or "brief") == "brief"):
                    self.prefetch_expand(c, user, turn.text)
            return turn

    _NO_DATA = re.compile(r"(?i)в руководстве[^.\n]{0,40}(данных|информации|сведений) нет|данных не нашёл"
                          r"|в руководстве (для|на) [^.\n]{0,120}\sнет(?=[\s.,;:—-])")

    def _log_gap(self, c: Conv, user: dict, question: str, turn: Turn) -> None:
        """Day-2 briefing: «no data» or a contradiction -> an automatic item for the chief / admin («пробел в базе»):
        who, which fleet, which vehicle and engine, what was asked. One per vehicle and topic a day."""
        if not c.vehicle or user.get("role") == "manager" or not question:
            return
        kinds = []
        if self._NO_DATA.search(turn.text or ""):
            kinds.append(("no_data", question))
        kinds += [("contradiction", k["topic"]) for k in c.contra]
        for kind, topic in kinds:
            try:
                day = time.time() - 86400
                dup = self.store.q("SELECT id FROM handoffs WHERE kind='kb_gap' AND vehicle_id=? AND ts>? AND note=?",
                                   (c.vehicle.vehicle_id, day, pii.mask(topic)[:300]))
                if dup:
                    continue
                payload = {"gap": kind, "question": pii.mask(question)[:300], "engine": c.vehicle.engine_model,
                           "vehicle_id": c.vehicle.vehicle_id, "company": user.get("company_id"), "mechanic": user.get("name") or user.get("id")}
                summary = ("Нет данных в руководстве" if kind == "no_data" else "Противоречие в руководствах") + \
                          f": {c.vehicle.engine_model}, машина {c.vehicle.vehicle_id}. Вопрос: {pii.mask(question)[:200]}"
                self.store.add_handoff(user.get("id") or c.uid, c.vehicle.vehicle_id, "kb_gap", pii.mask(topic)[:300], summary, payload)
                turn.tools.append({"tool": "kb_gap", "ok": True, "gap": kind})
            except Exception:
                pass

    def _ticket_held(self, c) -> str:
        """Policy's own sentence when a ticket is not freely available for this check ('' when it is)."""
        ch = c.check
        if not ch or ch.status != CheckStatus.anomaly:
            return ""
        refusal = policy.ticket_refusal(ch, c.vehicle)
        if refusal:
            return "Заявку не оформляю: " + refusal.rstrip(".") + "."
        if policy.doubt_flags(ch):
            return ("Сначала осмотр узла на месте по руководству. Заявку на ремонт оформите, только если осмотр подтвердит неисправность, "
                    "— при оформлении нужно будет подтвердить предупреждение.")
        return ""

    def resume_note(self, vehicle_id: str, user: dict | None = None) -> dict | None:
        """Day-2 briefing: a repair is interrupted (end of shift, no part) and resumed a day or two later, often by
        another mechanic. The session belongs to the VEHICLE: say where they stopped and recall past repairs of this
        truck (any age, «через полгода» too). Deterministic, no LLM."""
        lines, kind = [], None
        r = self.store.repair_open(vehicle_id)
        if r and r.get("steps"):
            n, cur = len(r["steps"]), min(r["current"], len(r["steps"]) - 1)
            st = r["steps"][cur]
            who = (self.store.user(r["user_id"]) or {}).get("name") or "другой механик"
            me = (user or {}).get("id") == r["user_id"]
            done = sum(1 for x in r["steps"] if x.get("done"))
            when = datetime.fromtimestamp(r.get("updated") or r["started"]).strftime("%d.%m %H:%M")
            lines.append(f"**Ремонт продолжается:** «{r['title']}». Остановились на шаге {cur + 1} из {n} (выполнено {done}): {st['text']} "
                         f"({st['document']}, стр. {st['page']}). " + ("Вы начали его" if me else f"Начал {who}") + f", последняя отметка {when}.")
            kind = "repair"
        try:
            past = [e for e in self.briefing._events(vehicle_id) if e.get("date")] if getattr(self, "briefing", None) else []
        except Exception:
            past = []
        past.sort(key=lambda e: e["date"], reverse=True)
        if past:
            def one(e):
                d = e["date"][8:10] + "." + e["date"][5:7] + "." + e["date"][:4]
                unit = self.fleet.fault_types.get(e.get("unit") or "", e.get("unit") or "")
                return f"{d} — {e['ticket_id']}" + (f", {unit}" if unit else "") + (f", {e['mechanic']}" if e.get("mechanic") else "")
            lines.append("**Ремонты этой машины:** " + "; ".join(one(e) for e in past[:3]) + ".")
            kind = kind or "history"
        if not lines:
            return None
        return {"kind": kind, "text": "\n".join(lines), "repair_id": r["id"] if r else None, "step": (r["current"] if r else None)}

    def marks_applicable(self, c: Conv) -> bool:
        ch = c.check
        return bool(c.vehicle and ch and ch.status == CheckStatus.anomaly and ch.anomaly_type in MARKS_CODES)

    def marks_state(self, c: Conv) -> str | None:
        """Latest timing-marks result saved for this vehicle and check: 'match' | 'mismatch' | None (kept in the store: survives instances)."""
        if not self.marks_applicable(c):
            return None
        for h in self.store.handoffs(vehicle_id=c.vehicle.vehicle_id, limit=50):
            pl = h.get("payload") or {}
            if h.get("kind") == "inspection" and pl.get("marks") and pl.get("session_id") == c.check.session_id:
                return pl["marks"]
        return None

    def record_marks(self, c: Conv, user: dict, matched: bool, note: str = "") -> int:
        """Save the mechanic's timing-marks result as an inspection record (no measured angle needed)."""
        finding = "метки совпали" if matched else "метки не совпадают"
        item = {"component": "Метки угла опережения впрыскивания топлива", "finding": finding,
                "severity": "внимание" if matched else "дефект", "source": "заметка", "confirmed": True}
        payload = {"items": [item], "marks": "match" if matched else "mismatch", "session_id": c.check.session_id,
                   "anomaly_type": c.check.anomaly_type, "check_open": True}
        summary = f"Сверка меток угла опережения: {finding} ({c.vehicle.vehicle_id}, {c.check.session_id})"
        hid = self.store.add_handoff(user.get("id") or c.uid, c.vehicle.vehicle_id, "inspection", pii.mask(note)[:500], summary, payload)
        self.store.audit(user_id=user.get("id") or c.uid, role=user.get("role") or "mechanic", vehicle_id=c.vehicle.vehicle_id,
                         action="inspection_marks", output=finding, ok=1)
        return hid

    def _marks_turn(self, c: Conv, user: dict, matched: bool, turn: Turn) -> str:
        """Record the result and build the LLM instruction for the next step (with prefetched pages)."""
        hid = self.record_marks(c, user, matched)
        turn.tools.append({"tool": "inspection_marks", "ok": True, "id": hid, "marks": "match" if matched else "mismatch"})
        if matched:
            c.hold_ticket = True
            c.draft = None
            c.preface = ("**Результат записан: метки угла опережения совпали.** Проверка не закрыта: диагностика показала отклонение "
                         "топливоподачи в целом, а ранний впрыск — только одна из причин стука. Заявку пока не оформляем.")
            qs = [f"{c.check.anomaly_title} стук жёсткая работа причины способы устранения", "неисправности топливной аппаратуры причины форсунки ТНВД"]
            ask = ("Механик сверил метки угла опережения впрыскивания: метки СОВПАЛИ (результат уже записан). Ранний впрыск этой проверкой не подтверждён, "
                   "но диагностика показала отклонение по топливоподаче в целом, а ранний впрыск — лишь одна из причин стука по руководству. "
                   "НЕ пиши, что двигатель исправен или что проверку можно закрыть; заявку не предлагай и prepare_ticket не вызывай. "
                   "Назови до 3 других причин по руководству этого двигателя и следующие проверки по порядку, каждую с меткой [S#].")
        else:
            qs = ["установка угла опережения впрыскивания топлива регулировка 5.12.1"]
            ask = ("Механик сверил метки угла опережения впрыскивания: метки НЕ СОВПАДАЮТ (результат осмотра записан; измеренное значение угла не нужно и не спрашивай его). "
                   "Это подтверждает ранний впрыск. Подготовь черновик заявки на регулировку угла опережения впрыскивания по п. 5.12.1 руководства: "
                   "вызови prepare_ticket, работа — с меткой S# и дословной цитатой из фрагмента. Механик отдельно подтвердит отправку кнопкой.")
        pre: list[dict] = []
        for q in qs:
            r = self.tool_search_manual(c, q, k=4)
            turn.tools.append({"tool": "search_manual", "args": {"query": q}, "ok": True, "auto": True,
                               "hits": [f"{f['label']}:{f['document']} стр.{f['pdf_page']}" for f in r["fragments"]]})
            pre += [f for f in r["fragments"] if f["label"] not in {x["label"] for x in pre}]
        if pre:
            ask += "\n\nФРАГМЕНТЫ РУКОВОДСТВА ЭТОГО ДВИГАТЕЛЯ (уже найдены):\n\n" + "\n\n".join(
                f"[{f['label']}] {f['document']}, стр. PDF {f['pdf_page']}\n{f['text'][:1300]}" for f in pre[:6])
        return ask

    def answer_actions(self, c: Conv, turn: Turn, intents: set[str]) -> list[dict]:
        """Buttons under an answer, from the conversation state (+ what the LLM tried to offer in words).
        kind=send: post `text` as the mechanic's message; open: page viewer; handoff: handoff sheet."""
        if any(cd.get("kind") in ("ticket_draft", "repair") for cd in turn.cards):
            return []  # those cards carry their own buttons
        vid = c.vehicle.vehicle_id
        rep = self.repair_card(vid)
        rep_open = bool(rep and rep.get("status") == "open")
        has_ticket = bool(self.store.tickets(vid))
        st = getattr(c.check, "status", None)
        status = getattr(st, "value", st)
        out: list[dict] = []
        if rep_open:
            out.append({"id": "next_step", "kind": "send", "label": "Следующий шаг", "text": "Готово, следующий шаг"})
        elif c.draft:
            out.append({"id": "confirm_ticket", "kind": "send", "label": "Подтвердить заявку", "text": "Подтверждаю заявку"})
        elif c.hold_ticket:
            pass  # marks matched: the check stays open, no ticket button
        elif not has_ticket and self.marks_applicable(c) and self.marks_state(c) is None:
            out.append({"id": "marks_mismatch", "kind": "send", "label": "Метки не совпадают", "text": "Метки не совпадают"})
            out.append({"id": "marks_match", "kind": "send", "label": "Метки совпали", "text": "Метки совпали"})
        elif status == "anomaly" and not has_ticket and policy.may_create_ticket(c.check, c.vehicle):
            out.append({"id": "create_ticket", "kind": "send", "label": "Создать заявку", "text": "Создать заявку на дефектовку"})
        elif has_ticket and ("repair" in intents or status == "anomaly"):
            out.append({"id": "start_repair", "kind": "send", "label": "Начать ремонт по шагам", "text": "Начать ремонт по шагам"})
        elif "ticket" in intents and status != "normal" and policy.may_create_ticket(c.check, c.vehicle):
            out.append({"id": "create_ticket", "kind": "send", "label": "Создать заявку", "text": "Создать заявку на дефектовку"})
        src = next((s for s in turn.sources if not s.get("extra")), turn.sources[0] if turn.sources else None)
        if src:
            out.append({"id": "show_section", "kind": "open", "label": "Показать раздел", "doc_id": src["doc_id"], "page": src["page"]})
        if "handoff" in intents or (status == "anomaly" and (has_ticket or rep_open)):
            out.append({"id": "handoff", "kind": "handoff", "label": "Передать главному"})
        return out[:3]

    def _handle(self, c: Conv, user: dict, text: str, image_note: str | None, event: str | None) -> Turn:
        turn = Turn()
        self.progress[c.cid] = []
        self.drafts[c.cid] = ""
        orig_event = event
        manager = user.get("role") in ("manager", "engineer") and c.vehicle is None
        raw = (text or "").strip()
        if manager and getattr(self, "briefing", None) and not event:
            t = self.briefing.route(c, user, raw, turn)  # deterministic: last scanned truck, insights, ticket + assignment
            if t is not None:
                return t
        marks_turn = False
        c.hold_ticket = False
        masked = raw  # depersonalized reversibly in Llm.chat right before it leaves the server (pii.Masker): the answer keeps the names
        if event == "expand":
            return self._expand(c, user, raw, turn)
        m = re.fullmatch(r"(?:код\s*(?:ошибки\s*)?)?(F\d{2})\.?", raw, re.I)
        if m and c.vehicle and not event:  # bare fault code typed or spoken -> grounded explanation by code
            code = m.group(1).upper()
            title = self.fleet.fault_types.get(code)
            raw = masked = f"Код неисправности {code}" + (f" «{title}»" if title else "") + ": что это значит для этого двигателя и что проверить по руководству?"
        if event == "triage" and c.vehicle:
            order = self.triage(c.vehicle.vehicle_id)
            if len(order) < 2:
                turn.text = "Сейчас у машины одно отклонение — порядок не нужен, работаем по нему."
                return turn
            c.preface = "**Порядок работ по приоритету** (правила срочности ТЗ):\n" + "\n".join(
                f"{i}. {o['code']} {o['title']} — {o['urgency']}: {'; '.join(o['why'][:2])}" for i, o in enumerate(order, 1))
            raw = masked = ("Несколько отклонений по этой машине, порядок уже определён: " + ", ".join(f"{o['code']} {o['title']}" for o in order) +
                            ". Для каждого в этом порядке: до 3 вероятных причин и первая проверка по руководству этого двигателя, с метками. "
                            "Норматив времени на работу — только если он есть во фрагменте, иначе напиши «время в книге не указано». Порядок не меняй.")
            event = None
        if c.vehicle and not event and raw:
            norm, changes = self.normalizer.normalize(masked)
            if changes:  # slang / typos -> manual terms; the model sees both, the mechanic sees "understood as"
                turn.understood = changes
                masked = f"{masked}\n(в терминах руководства: {norm})"
        if event in ("alternatives", "hypothesis") and c.vehicle:
            raw = masked = (ALT_PROMPT if event == "alternatives" else HYPOTHESIS_PROMPT.format(h=masked[:300]))
            event = None
        # deterministic fast paths (voice-friendly, no LLM)
        if c.vehicle and not event:
            if c.draft and CONFIRM_RE.match(raw):
                return self._confirm_turn(c, user, inspection_ok=True, turn=turn)
            if c.draft and CANCEL_RE.match(raw):
                c.draft = None
                turn.text = "Заявка отменена, в ТОиР ничего не отправлено."
                return turn
            if self.marks_applicable(c) and (MARKS_NO_RE.match(raw) or MARKS_YES_RE.match(raw)):
                masked = self._marks_turn(c, user, bool(MARKS_YES_RE.match(raw)), turn)
                marks_turn = True
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
            mm = policy.mismatch(c.check, c.vehicle)
            if mm:  # organizers 26.11: the card wins, it is a model error -- nothing to explain about a fault this engine can not have
                turn.text = (mm + " Что сделать: повторите запись (двигатель на холостом ходу, микрофон под двигателем, 20–30 секунд) "
                             "и сверьте модель двигателя в карточке с табличкой на блоке. Если шум или дым есть на самом деле — проведите дефектовку "
                             "по руководству двигателя из карточки.")
                turn.tools.append({"tool": "policy", "ok": True, "decision": c.decision.value if c.decision else None})
                return turn
            ch = c.check
            pre = []
            for q in (f"{ch.anomaly_title} {ch.description}", f"неисправности причины способы устранения {ch.anomaly_title}"):
                r = self.tool_search_manual(c, q, k=4)
                turn.tools.append({"tool": "search_manual", "args": {"query": q}, "ok": True, "auto": True,
                                   "hits": [f"{f['label']}:{f['document']} стр.{f['pdf_page']}" for f in r["fragments"]]})
                pre += [f for f in r["fragments"] if f["label"] not in {x["label"] for x in pre}]
            frag_txt = "\n\n".join(f"[{f['label']}] {f['document']}, стр. PDF {f['pdf_page']}\n{f['text']}" for f in pre[:7])
            brief = (user.get("detail") or "brief") == "brief"
            masked = (f"Объясни механику результат текущей проверки ({'коротко, до 6 строк' if brief else 'до 12 строк'}): вердикт; что вероятно не так; чем грозит; что и как проверить "
                      "(допуски, порядок со страницами); в конце " + ("что делать дальше по правилу проверки, заявку не предлагай" if self._ticket_held(c) else "предложи создать заявку")
                      + ". Ниже — уже найденные фрагменты руководства этого двигателя; "
                      "если в них нет нужного — сделай не более одного дополнительного поиска.\n\n" + frag_txt)
        if image_note:
            masked = (masked + "\n" if masked else "") + ("[Материал от механика (не ML-диагноз), описание: " + image_note + "]")
        ticket_ask = bool(c.vehicle and c.check and c.check.status == CheckStatus.anomaly and TICKET_RE.search(raw or "") and not c.draft and not marks_turn)
        if ticket_ask:
            c.no_instruction = None
            refusal = policy.ticket_refusal(c.check, c.vehicle)
            if refusal:  # policy, not the model: no LLM call, the same answer every time
                turn.text = "Заявку не оформляю: " + refusal.rstrip(".") + "."
                turn.tools.append({"tool": "policy", "ok": True, "decision": c.decision.value if c.decision else None})
                return turn
            if policy.doubt_flags(c.check):  # warning / 0,50–0,60: the mechanic asks himself -> the draft; the card asks to confirm the warning
                masked = (masked or "") + ("\n(Механик сам просит заявку на ремонт: вызови prepare_ticket. Предупреждение о сомнении покажет карточка, "
                                           "отправка в ТОиР — только после его подтверждения.)")
        if not self.llm.available:
            return self._fallback(c, masked, turn)
        if (self.facts_pass and c.vehicle and not manager and not orig_event and raw and not ticket_ask and not image_note and not marks_turn
                and not c.draft and not PREFETCH_SKIP.search(raw)):
            t = self._two_pass(c, user, raw, turn)
            if t is not None:
                return t
        if c.vehicle and not event and raw and not marks_turn and (ticket_ask or not PREFETCH_SKIP.search(raw)):
            # Retrieval before the first LLM call: the model usually answers in ONE round instead of
            # "decide to search" + "answer" (≈ half the latency). It may still search again if fragments do not fit.
            # For a ticket request the procedures for the current fault are prefetched, so prepare_ticket gets quotes at once.
            q = (f"{c.check.anomaly_title} {c.check.description} проверка регулировка замена" if ticket_ask
                 else self.normalizer.normalize((pii.mask(raw) if self.pii_masking else raw) + (" " + _media_query(image_note) if image_note else ""))[0])
            codes = [(m.upper(), self.fleet.fault_types.get(m.upper())) for m in re.findall(r"(?i)\b(f\d{2})\b", raw)]
            codes = [(k, t) for k, t in codes if t]
            if codes and not ticket_ask:  # "что значит F08?": search by the fault group name, not by the code
                q = " ".join(t for _, t in codes) + " неисправности причины проверка " + re.sub(r"(?i)\bf\d{2}\b", "", q)
                masked += "\n(" + "; ".join(f"{k} — код группы неисправностей акустической ML-диагностики: «{t}»" for k, t in codes) + ")"
            r = self.tool_search_manual(c, q, k=5)
            self.progress.setdefault(c.cid, []).append(PROGRESS["search_manual"] + f": {q[:80]}")
            turn.tools.append({"tool": "search_manual", "args": {"query": q}, "ok": True, "auto": True,
                               "hits": [f"{f['label']}:{f['document']} стр.{f['pdf_page']}" for f in r["fragments"]]})
            if r["fragments"]:
                masked += ("\n\nФРАГМЕНТЫ РУКОВОДСТВА ЭТОГО ДВИГАТЕЛЯ ПО ВОПРОСУ (уже найдены — отвечай по ним с метками; "
                           "новый search_manual — только если ответа в них нет; если вопрос не о двигателе или данных нет — так и скажи):\n\n" +
                           "\n\n".join(f"[{f['label']}] {f['document']}, стр. PDF {f['pdf_page']}" + (f" — {f['section']}" if f.get("section") else "") +
                                        f"\n{f['text'][:1300]}" for f in r["fragments"][:6]))
            if not ticket_ask and not codes and self.extra and (c.vehicle.make or "").upper() in self.extra.makes():
                # The engine manual had no answer (the grounded pass declined) or the question is about the chassis:
                # this truck's own vehicle manual (or the labelled sibling) is searched up front instead of hoping the model calls the tool.
                x = self.tool_search_extra(c, q, k=5)
                if x["fragments"]:
                    self.progress.setdefault(c.cid, []).append(PROGRESS["search_extra_docs"] + f": {q[:80]}")
                    turn.tools.append({"tool": "search_extra_docs", "args": {"query": q}, "ok": True, "auto": True,
                                       "hits": [f"{f['label']}:{f['document']} стр.{f['pdf_page']}" for f in x["fragments"]]})
                    masked += (f"\n\nФРАГМЕНТЫ РУКОВОДСТВА ПО АВТОМОБИЛЮ {c.vehicle.model or c.vehicle.make} (доп. источник, метки X#: шасси, кабина, "
                               "тормоза, шины, сцепление, КПП, мосты; нормативы двигателя — только из S#; если фрагмент не про вопрос — не используй):\n\n" +
                               "\n\n".join(f"[{f['label']}] {f['document']}, стр. PDF {f['pdf_page']}\n{f['text'][:1300]}" for f in x["fragments"]))

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
                msg, tok = self.llm.chat(f"agent_r{rnd}", messages, tools=tools if rnd < self.max_rounds else None, max_tokens=4500,
                                         on_text=self._live(c, messages))
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
                drafted = False
                for tc in calls:
                    res = self._run_tool(c, user, tc.function.name, tc.function.arguments, turn)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(res, ensure_ascii=False)[:12000]})
                    drafted = drafted or (tc.function.name == "prepare_ticket" and bool(res.get("draft_ready")))
                if drafted and c.draft:
                    # The draft card is the answer: no extra LLM round to retell it (Vercel 26.11: 20–30 s per draft,
                    # a third of it the model paraphrasing the card). The text is built from the verified draft.
                    answer = self._draft_text(c, ty=(re.search(r"(?i)на «(ты|вы)»", messages[0]["content"]) or [None, "вы"])[1] == "ты")
                    turn.tools.append({"tool": "draft_text", "auto": True})
                    break
            if not any(t["tool"] == "draft_text" for t in turn.tools):
                answer = self._verify_answer(c, messages, answer, turn)
        except LlmUnavailable as e:
            turn.ok = False
            turn.tools.append({"tool": "llm", "error": str(e)})
            return self._fallback(c, masked, turn)
        turn.text = answer or "Не удалось сформировать ответ. Повторите вопрос."
        turn.sources = [self._source_view(l, c) for l in cited_labels(turn.text) if l in c.frags]
        if c.draft and any(t["tool"] == "prepare_ticket" and t.get("ok") for t in turn.tools):
            turn.cards.append(self.draft_card(c))
        elif c.no_instruction and not c.draft and any(t["tool"] == "prepare_ticket" for t in turn.tools):
            turn.cards.append({"kind": "no_instruction", "reason": "В руководстве на двигатель этой машины нет инструкции по найденной неисправности — заявка не отправлена.",
                               "recommendation": c.no_instruction})
        if any(t["tool"] in ("start_repair", "update_repair") for t in turn.tools) and c.vehicle:
            card = self.repair_card(c.vehicle.vehicle_id)
            if card:
                turn.cards.append(card)
        return turn

    def _two_pass(self, c: Conv, user: dict, raw: str, turn: Turn) -> Turn | None:
        """Facts once, style per person (agent/grounded.py). None -> the usual agent loop (no answer in the manual, small talk)."""
        from . import style as ustyle

        q = self.normalizer.normalize(pii.mask(raw) if self.pii_masking else raw)[0]
        codes = [(m.upper(), self.fleet.fault_types.get(m.upper())) for m in re.findall(r"(?i)\b(f\d{2})\b", raw)]
        codes = [(k, t) for k, t in codes if t]
        if codes:  # "что значит F08?": search by the fault group name, not by the code
            q = " ".join(t for _, t in codes) + " неисправности причины проверка " + re.sub(r"(?i)\bf\d{2}\b", "", q)
        detail = user.get("detail") or "brief"
        self.progress.setdefault(c.cid, []).append(PROGRESS["search_manual"] + f": {q[:80]}")
        facts = self.fp.facts(c, q, detail, turn)
        if not facts:
            return None
        facts = self.fp.labelled(c, facts)
        persona = ustyle.persona_of(user)
        try:
            ans = self.fp.render(c, user, raw, facts, persona, detail, LANGS[answer_lang(user)]["llm"], turn)
        except LlmUnavailable as e:
            turn.tools.append({"tool": "llm", "error": str(e)})
            ans = self.fp.plain(facts, persona)
        turn.text = ans
        turn.sources = [self._source_view(l, c) for l in cited_labels(ans) if l in c.frags]
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
        # short result for the engineer's agent journal: scalar fields only, no manual text
        log["result"] = {k: (v[:120] if isinstance(v, str) else v) for k, v in res.items()
                         if k not in ("fragments", "error") and isinstance(v, (str, int, float, bool)) and v is not None}
        if not log["result"]:
            del log["result"]
        turn.tools.append(log)
        return res

    def _allowed_numbers(self, c: Conv, messages: list[dict]) -> set[str]:
        """Numbers that may appear without a manual fragment: check card, vehicle card, tickets, what the mechanic typed."""
        from .grounding import numbers

        allowed: set[str] = set()
        if c.check:
            allowed |= numbers(json.dumps(c.check.model_dump(mode="json"), ensure_ascii=False))
        if c.vehicle:
            allowed |= numbers(c.vehicle.model_dump_json())
        for t in self.store.tickets(c.vehicle.vehicle_id) if c.vehicle else []:
            allowed |= {t["ticket_id"].split("-")[-1].lstrip("0")}
        if c.draft:
            allowed |= {str(len(c.draft["operations"])), str(len(c.draft["spare_parts"]))}
        for m in messages:  # numbers the mechanic typed himself may be echoed back
            if m.get("role") == "user" and isinstance(m.get("content"), str) and not m["content"].startswith("Проверка:"):
                allowed |= numbers(m["content"])
        return allowed

    def _live(self, c: Conv, messages: list[dict], sink: dict | None = None):
        """Callback for the streamed answer: publish only COMPLETE lines whose numbers are all found in the
        fragments (same rule as the final number guard), so the mechanic reads the answer while it is written
        but never sees an unverified figure. The final verified answer replaces this draft."""
        allowed = self._allowed_numbers(c, messages)
        evidence = [f["text"] for f in c.frags.values()]
        state = {"n": 0}

        def cb(text: str) -> None:
            if text.lstrip().startswith("{"):
                return  # JSON tool payloads are not for the screen
            lines = text.split("\n")[:-1]  # the last line may be incomplete
            if len(lines) <= state["n"]:
                return
            state["n"] = len(lines)
            ok = []
            for ln in lines:
                ln = re.sub(r"\[(?:S|X)\d+\]", "", ln).rstrip()
                if ln.strip() and not unverified_numbers(ln, evidence, allowed):
                    ok.append(ln)
            if sink is not None:
                sink["draft"] = "\n".join(ok)[-3000:]  # background run: not this conversation's live draft
            else:
                self.drafts[c.cid] = "\n".join(ok)[-3000:]
        return cb

    def _verify_answer(self, c: Conv, messages: list[dict], answer: str, turn: Turn) -> str:
        if not answer:
            return answer
        # drop citations to labels that do not exist
        for lab in cited_labels(answer):
            if lab not in c.frags:
                answer = answer.replace(f"[{lab}]", "")
        evidence = [f["text"] for f in c.frags.values()]
        allowed = self._allowed_numbers(c, messages)
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
            answer += "\n\nЧасть цифр не подтверждена руководством и убрана — уточните по странице."
            turn.tools.append({"tool": "number_guard", "removed": bad})
        return drop_unasked_nodata(answer)

    def triage(self, vehicle_id: str, last_n: int = 6) -> list[dict]:
        """Item 13: distinct anomalies in the recent checks, ordered by the TZ urgency rules, then by score."""
        rank = {"high": 0, "medium": 1, "low": 2}
        latest: dict[str, CheckResult] = {}
        for ch in sorted(self.fleet.sessions(vehicle_id), key=lambda x: x.checked_at or "")[-last_n:]:
            if getattr(ch.status, "value", ch.status) == "anomaly" and ch.anomaly_type:
                latest[ch.anomaly_type] = ch
        out = []
        for code, ch in latest.items():
            u, why = policy.urgency(ch)
            out.append({"code": code, "title": ch.anomaly_title or self.fleet.fault_types.get(code, ""), "urgency": u.value, "why": why,
                        "score": ch.anomaly_score, "session_id": ch.session_id})
        return sorted(out, key=lambda o: (rank.get(o["urgency"], 3), -(o["score"] or 0)))

    def summary(self, c: Conv, user: dict) -> dict:
        from .handoff import summarize

        return summarize(self, c, LANGS[answer_lang(user)]["llm"])

    def handoff(self, c: Conv, user: dict, note: str = "") -> dict:
        s = self.summary(c, user)
        hid = self.store.add_handoff(user["id"], c.vehicle.vehicle_id, "handoff", pii.mask(note)[:500], s["text"], s["package"])
        return {"id": hid, "summary": s["text"], "pages": s["package"]["pages"], "photos": s["package"]["photos"]}

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
            turn.text = NO_DATA_TEXT
            return turn
        lines = [FALLBACK_HEAD]
        for f in res["fragments"][:3]:
            lines.append(f"[{f['label']}] {f['document']}, стр. {f['pdf_page']}: {f['text'][:400].strip()}…")
        turn.text = "\n\n".join(lines)
        turn.sources = [self._source_view(f["label"], c) for f in res["fragments"][:3]]
        return turn

    # ------------------------------------------------------------------ ticket confirmation gate
    TIME_RX = re.compile(r"(?:трудо[её]мкость|норм\w* времени|время выполнения|продолжительность)[^.\n]{0,80}?\d[\d,.]*\s*(?:чел\.?[- ]?ч|ч\b|час\w*|мин\w*)"
                         r"|\d[\d,.]*\s*(?:чел\.?[- ]?ч\w*|нормо[- ]?час\w*)", re.I)

    def labor_norms(self, c: Conv, operations: list[dict]) -> list[dict]:
        """Item 14: labour time per operation, only if the cited manual section states it (verbatim), else 'not in the book'."""
        out = []
        for o in operations:
            sec = self.sections.find(o.get("doc_id"), o.get("page"), o.get("quote") or o.get("text") or "") if o.get("doc_id") else None
            m = self.TIME_RX.search(sec.text) if sec else None
            if m:
                line = next((p for p in sec.pages if m.group(0) in p["text"]), sec.pages[0])
                out.append({"text": o.get("text"), "norm": m.group(0).strip(), "doc_id": o["doc_id"], "page": line["page"]})
            else:
                out.append({"text": o.get("text"), "norm": None})
        return out

    def _draft_text(self, c: Conv, ty: bool = False) -> str:
        """Short text over the verified draft (the card lists the works): how many, from which pages, priority with its basis, next step."""
        d = c.draft
        u = (d.get("request") or {}).get("urgency") or ""
        ops, parts = d.get("operations") or [], d.get("spare_parts") or []
        labs = list(dict.fromkeys(it["label"] for it in ops + parts if it.get("label")))
        n = len(ops)
        works = "работа" if n % 10 == 1 and n % 100 != 11 else "работы" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "работ"
        basis = d.get("urgency_basis") or []
        basis = "; ".join(basis) if isinstance(basis, list) else str(basis)
        m = len(parts)
        pw = "запчасть" if m % 10 == 1 and m % 100 != 11 else "запчасти" if m % 10 in (2, 3, 4) and m % 100 not in (12, 13, 14) else "запчастей"
        # The page chips get a short line of their own (at 390 px they wrapped under a long first line, a lone «с. 53» there)
        out = [f"Черновик заявки на дефектовку готов: {n} {works}" + (f" и {m} {pw}" if parts else "") + "."]
        if labs:
            out.append("Взято из руководства: " + " ".join(f"[{l}]" for l in labs))  # no dot after the chips
        out += [f"Приоритет — {URG_RU.get(u, u)}." + (f" Основание: {basis.rstrip('.')}." if basis else "")]
        if d.get("requires_inspection"):
            out.append("Результат проверки под сомнением — перед отправкой " + ("подтверди" if ty else "подтвердите") + " неисправность осмотром.")
        out.append("Проверь карточку и подтверди отправку в ТОиР — без подтверждения ничего не уходит." if ty else
                   "Проверьте карточку и подтвердите отправку в ТОиР — без подтверждения ничего не уходит.")
        return "\n".join(out)

    def draft_card(self, c: Conv) -> dict:
        d = c.draft
        return {"kind": "ticket_draft", "draft_id": d["id"], "request": d["request"], "operations": d["operations"], "spare_parts": d["spare_parts"],
                "urgency_basis": d["urgency_basis"], "requires_inspection": d["requires_inspection"], "labor": self.labor_norms(c, d["operations"]),
                "prohibition": d.get("prohibition"), "urgency_options": d.get("urgency_options") or policy.URGENCY_MEANING_FULL,
                "warning": "; ".join(policy.doubt_flags(c.check)) if c.check else ""}

    def confirm(self, c: Conv, draft_id: str | None, inspection_ok: bool = True, urgency: str | None = None) -> dict:
        if not c.draft or (draft_id and draft_id != c.draft["id"]):
            return {"error": "нет черновика для подтверждения"}
        if not inspection_ok:
            c.draft = None
            return {"cancelled": True, "text": "Осмотр не подтвердил неисправность — заявка не отправлена, машина может вернуться на линию. Результат записан в журнал."}
        if not policy.may_create_ticket(c.check, c.vehicle):
            c.draft = None
            return {"error": "по текущей проверке заявка не допускается: " + (policy.ticket_refusal(c.check, c.vehicle) or "")}
        if urgency in ("high", "medium", "low") and urgency != c.draft["request"].get("urgency"):
            # the mechanic's choice on confirm is final (organizers 26.11); the proposed level stays in the basis
            c.draft["request"]["urgency"] = urgency
            c.draft["urgency_basis"] = list(c.draft.get("urgency_basis") or []) + [f"механик изменил приоритет на «{URG_RU[urgency]}»"]
        req = TicketRequest(**c.draft["request"])
        ack = self.toir.create_ticket(req)
        self.store.add_ticket(c.uid, c.draft["request"], ack.ticket_id)
        try:  # item 7: the ticket carries its context (answer, pages, photos, summary), not just the JSON
            from .handoff import package, _plain

            p = package(self, c)
            last = next((d["text"] for d in reversed(p["dialog"]) if d["role"] == "assistant"), "")
            self.store.set_ticket_attachments(ack.ticket_id, {"summary": _plain(p), "answer": last[:2500], "pages": p["pages"],
                                                              "photos": p["photos"], "check": p["check"], "inspection": p.get("inspection")})
        except Exception:
            pass
        card = {"kind": "ticket_created", "ticket_id": ack.ticket_id, "request": c.draft["request"]}
        c.draft = None
        return {"ticket_id": ack.ticket_id, "card": card, "text": f"Заявка {ack.ticket_id} создана в ТОиР."}

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
