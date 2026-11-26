"""Two-pass answers for manual questions: FACTS once, STYLE per person.

Pass 1 (persona-independent, cached per (vehicle, question, detail)): the same retrieval as the chat prefetch, then one
LLM call (temperature 0) that copies the facts answering the question out of the fragments into a short list. Every
fact keeps its page; a fact whose numbers are not in its own fragment is dropped.
Pass 2 (per person): the facts are retold in this person's manner (style card, experience, memory callback). The
validator accepts the text only if (a) every number in it is a number of some fact (or of the vehicle / check card),
(b) every fact is present: all its numbers and its source label, (c) the line limit of the person holds. One retry
with the list of problems, then a deterministic rendering of the facts (still the same facts and pages).
So two people asking the same question about the same truck get the same pages and the same figures.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from .grounding import numbers

FACTS_SYSTEM = """Ты извлекаешь факты из фрагментов руководства по ремонту двигателя, чтобы ответить на вопрос механика.
Верни JSON: {{"answerable": true|false, "facts": [{{"src": "S1", "text": "..."}}]}}.
Правила:
- Только то, что прямо написано во фрагментах и отвечает на вопрос. Ничего не додумывай.
- Каждый факт — одна короткая законченная мысль (до 120 знаков): норматив, проверка, действие, причина или предупреждение.
- Без перечислений моделей и модификаций двигателей, без номеров рисунков; одинаковое не повторяй.
- Числа, диапазоны и единицы — ровно как во фрагменте (0,25–0,30 мм; 118–147 Н·м). Не пересчитывай, не округляй.
- src — метка фрагмента, где написан факт.
- {limit}. Сначала главное: прямой ответ (норматив), потом как проверить, потом что сделать.
- Если во фрагментах нет ответа на вопрос или вопрос не о двигателе — {{"answerable": false, "facts": []}}.
- Пиши по-русски, нейтрально, без обращения к человеку."""

RENDER_SYSTEM = """Ты «Цифровой механик», помощник механика. Перескажи ФАКТЫ человеку его манерой, на {lang} языке.
Обязательно:
- Используй ВСЕ факты и только их. Своих цифр, страниц, нормативов и советов с цифрами не добавляй.
- Каждое число и единицу пиши ровно как в факте. После каждого факта ставь его метку в квадратных скобках, например [S2].
- Можно объединять факты в одну строку, менять порядок слов, говорить своими словами.
- {length}
- Без вступлений, без пересказа вопроса, без списка источников и без предложений «создать заявку» — кнопки покажет интерфейс.
- Указания о стиле ниже — для тебя, не пересказывай их человеку.
КАК ГОВОРИТЬ С ЭТИМ ЧЕЛОВЕКОМ:
{style}"""

LIMITS = {"brief": "Не больше 4 фактов — только самые нужные для ответа", "detailed": "Не больше 10 фактов"}
FACTS_V = "v3"  # bump when the extraction rules change: old cache entries are not reused
# lines per persona for the brief answer (the veteran asked for at most 4 short lines)
MAX_LINES = {"veteran": 4, "selftaught": 6, "student": 7, "manager": 5, "engineer": 6}
LEN_RULE = {
    "veteran": ("Не больше 4 строк, каждая до 110 знаков, лучше 2-3: сначала цифра, потом метка. Каждая строка с новой строки. "
                "Цифры одного узла — в одну строку, каждую цифру один раз. Порядок действий — одной строкой через «→» "
                "(контргайку отпустить → щуп → винтом зазор → затянуть). "
                "Его словами (клапана, форсы, коленвал), без вводных слов, без «проверь/регулируй» в каждой строке, без пояснений и без техники безопасности."),
    "selftaught": ("До 6 строк, каждая с новой строки. Нормативы, допуски и моменты — с цифрами. Подача: «по книге так-то» — уважительно, "
                   "как опытному: он руками это делал, ему нужна точная цифра и страница."),
    "student": ("До 7 строк, каждая с новой строки и с глагола (Проверь, Сними, Затяни). Первая строка — коротко про безопасность "
                "(заглуши, дай остыть). Новый термин поясни в скобках двумя-тремя словами. Последняя строка — как понять, что всё получилось, "
                "или ободрение. Спокойно и по-доброму."),
}
LABEL = re.compile(r"\[(S\d+)\]")


def _key(vehicle_id: str, question: str, detail: str) -> str:
    q = re.sub(r"[^\w]+", " ", (question or "").lower()).strip()
    return hashlib.sha1(f"{FACTS_V}|{vehicle_id}|{detail}|{q}".encode()).hexdigest()


def _nums(text: str) -> set[str]:
    """Numbers that matter for parity: not labels, not page refs, not model codes."""
    t = re.sub(r"\[[SX]\d+\]", " ", text or "")
    t = re.sub(r"(?m)^\s*\d{1,2}[.)]\s", " ", t)  # list numbering is layout, not a fact
    t = re.sub(r"(?i)(стр\.?|с\.)\s*\d+", " ", t)
    t = re.sub(r"[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z]{1,6}-\d[\w.]*|\b[A-ZА-ЯЁ]\d{3}(?:[A-ZА-ЯЁ]{2}\d{2,3})?\b|\bF\d{2}\b", " ", t)
    return numbers(t)


class FactPipeline:
    def __init__(self, agent):
        self.a = agent
        agent.store.x("CREATE TABLE IF NOT EXISTS facts_cache(k TEXT PRIMARY KEY, vehicle_id TEXT, question TEXT, facts TEXT, ts REAL)")

    # ---------------------------------------------------------------- pass 1
    def facts(self, c, question: str, detail: str, turn) -> list[dict] | None:
        """[{"doc_id","page","text"}] or None when the manual has no answer (the caller falls back to the agent loop)."""
        vid = c.vehicle.vehicle_id
        k = _key(vid, question, detail)
        row = self.a.store.one("SELECT facts FROM facts_cache WHERE k=?", (k,))
        if row:
            facts = json.loads(row["facts"])
            turn.tools.append({"tool": "facts", "cached": True, "n": len(facts)})
            return facts or None
        t0 = time.time()
        r = self.a.tool_search_manual(c, question, k=5)
        frags = r["fragments"][:7]
        turn.tools.append({"tool": "search_manual", "args": {"query": question}, "ok": True, "auto": True,
                           "hits": [f"{f['label']}:{f['document']} стр.{f['pdf_page']}" for f in frags]})
        if not frags:
            return None
        text = "\n\n".join(f"[{f['label']}] {f['document']}, стр. {f['pdf_page']}" + (f" — {f['section']}" if f.get("section") else "")
                           + f"\n{f['text'][:1400]}" for f in frags)
        try:
            out = self.a.llm.json("facts", FACTS_SYSTEM.format(limit=LIMITS.get(detail, LIMITS["brief"])),
                                  f"Вопрос механика: {question}\n\nФРАГМЕНТЫ:\n\n{text}", max_tokens=1500)
        except Exception as e:  # noqa: BLE001 - LLM down or bad JSON: the agent loop handles it
            turn.tools.append({"tool": "facts", "error": str(e)[:200]})
            return None
        by_label = {f["label"]: f for f in frags}
        facts, dropped = [], []
        for f in (out.get("facts") or [])[:10] if out.get("answerable", True) else []:
            src, t = str(f.get("src", "")).strip("[] "), str(f.get("text", "")).strip()
            fr = c.frags.get(src)
            if not t or src not in by_label or not fr:
                dropped.append(t[:60]); continue
            if not _nums(t) <= numbers(fr["text"]):  # a figure that is not on its page
                dropped.append(t[:60]); continue
            ns = _nums(t)
            if ns and any(ns == _nums(x["text"]) for x in facts):  # the same figure from another page: once is enough
                dropped.append(t[:60]); continue
            facts.append({"doc_id": fr["doc_id"], "page": fr["page"], "text": t})
        self.a.store.x("INSERT OR REPLACE INTO facts_cache(k, vehicle_id, question, facts, ts) VALUES (?,?,?,?,?)",
                       (k, vid, question[:300], json.dumps(facts, ensure_ascii=False), time.time()))
        turn.tools.append({"tool": "facts", "cached": False, "n": len(facts), "dropped": dropped, "ms": int((time.time() - t0) * 1000)})
        return facts or None

    def labelled(self, c, facts: list[dict]) -> list[dict]:
        """Attach this conversation's labels (S#) to the facts' pages."""
        out = []
        for f in facts:
            page = self.a.index.get_page(f["doc_id"], f["page"])
            lab = self.a._add_frag(c, "S", doc_id=f["doc_id"], document=page.document if page else f["doc_id"], page=f["page"],
                                   text=page.text if page else f["text"], extra=False)
            out.append({**f, "label": lab})
        return out

    # ---------------------------------------------------------------- pass 2
    def check(self, answer: str, facts: list[dict], allowed: set[str], persona: str, detail: str) -> list[str]:
        problems = []
        have = set().union(*(_nums(f["text"]) for f in facts)) | allowed
        extra = sorted(n for n in _nums(answer) if n not in have)
        if extra:
            problems.append("лишние числа, которых нет в фактах: " + ", ".join(extra))
        labels = set(LABEL.findall(answer))
        bad_lab = sorted(labels - {f["label"] for f in facts})
        if bad_lab:
            problems.append("метки не из фактов: " + ", ".join(bad_lab))
        got = _nums(answer)
        for i, f in enumerate(facts, 1):
            miss = sorted(_nums(f["text"]) - got)
            if miss or f["label"] not in labels:
                problems.append(f"факт {i} [{f['label']}] потерян" + (f" (нет чисел {', '.join(miss)})" if miss else " (нет метки)"))
        lines = [ln for ln in answer.splitlines() if ln.strip()]
        lim = MAX_LINES.get(persona) if detail == "brief" else None
        body = lines[1:] if lines and "?" in lines[0] and not LABEL.search(lines[0]) else lines  # memory callback line
        if lim and len(body) > lim:
            problems.append(f"строк {len(body)}, можно не больше {lim}")
        if persona == "veteran" and detail == "brief":
            long = [i + 1 for i, ln in enumerate(body) if len(LABEL.sub("", ln).strip()) > 120]
            if long:
                problems.append("слишком длинные строки " + ", ".join(map(str, long)) + " (до 110 знаков, разбей или сожми)")
        return problems

    def render(self, c, user: dict, question: str, facts: list[dict], persona: str, detail: str, lang: str, turn) -> str:
        style = "\n".join(user[k] for k in ("_style", "_memory") if user.get(k)) or "Просто и по делу, на «ты»."
        length = LEN_RULE.get(persona, "До 6 строк.") if detail == "brief" else "Подробно, по шагам, до 14 строк."
        listing = "\n".join(f"{i}. [{f['label']}] (стр. {f['page']}) {f['text']}" for i, f in enumerate(facts, 1))
        msgs = [{"role": "system", "content": RENDER_SYSTEM.format(lang=lang, length=length, style=style)},
                {"role": "user", "content": f"Вопрос: {question}\n\nФАКТЫ:\n{listing}"}]
        allowed = self.a._allowed_numbers(c, [])
        for attempt in range(2):
            msg, tok = self.a.llm.chat(f"render_{persona}", msgs, max_tokens=1200, temperature=0.2, on_text=self.a._live(c, msgs))
            turn.tokens += tok
            ans = (msg.content or "").strip()
            problems = self.check(ans, facts, allowed, persona, detail)
            if not problems:
                turn.tools.append({"tool": "render", "persona": persona, "attempts": attempt + 1, "ok": True})
                return ans
            msgs += [{"role": "assistant", "content": ans},
                     {"role": "user", "content": "Проверка не прошла: " + "; ".join(problems) + ". Перепиши: все факты, только их числа, метки на месте."}]
        turn.tools.append({"tool": "render", "persona": persona, "attempts": 2, "ok": False, "fallback": True, "problems": problems})
        return self.plain(facts, persona)

    @staticmethod
    def plain(facts: list[dict], persona: str) -> str:
        """Deterministic rendering: the facts themselves, joined for the veteran's 4-line limit."""
        rows = [f"{f['text'].rstrip('.')} [{f['label']}]." for f in facts]
        if persona == "veteran" and len(rows) > 4:
            rows = rows[:3] + [" ".join(rows[3:])]
        return "\n".join(rows)
