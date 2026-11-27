"""Repair-intent extraction after STT (V t565u): in a workshop the recording catches a customer, another master,
machines. The raw transcript + the current context (vehicle, check, repair step) go to the LLM, which returns the
mechanic's repair question/command reconstructed cleanly, the dropped off-topic fragments and a confidence.
Guards: short clean phrases skip the LLM (no extra latency for «дальше», «готово», a one-sentence question);
a number that was not spoken is never added (the raw text is used instead and the mechanic confirms);
low confidence -> «Я понял так: … Верно?»."""
from __future__ import annotations

import re

from .. import ru_tts

SYSTEM = """Ты разбираешь распознанную речь механика в шумном цехе. В записи могут быть чужие голоса (клиент, другой мастер), обрывки фраз и мусор распознавания.
Верни только JSON: {"repair_utterance": "...", "dropped": ["..."], "confidence": 0.0}
- repair_utterance — вопрос или команда механика по ремонту, диагностике или обслуживанию этой машины, восстановленные одной чистой фразой на языке записи. Только то, что было сказано: не добавляй чисел, единиц, деталей, узлов и действий, которых нет в записи. Числа — только прозвучавшие.
- dropped — дословные фрагменты не по делу (разговоры, вопросы клиента не о ремонте, шум).
- Если по ремонту в записи ничего нет — repair_utterance "" и confidence 0.
- confidence — от 0 до 1: насколько уверен, что восстановил именно то, что хотел механик."""

CONFIRM_BELOW = 0.7
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def needs_llm(raw: str) -> bool:
    """A short single phrase is taken as is; long or multi-sentence transcripts (several voices, chatter) go to the LLM."""
    words = len(raw.split())
    sentences = len([s for s in re.split(r"[.!?…]+", raw) if len(s.split()) >= 2])
    return words > 10 or sentences >= 2


def invented_numbers(utt: str, raw: str) -> list[str]:
    """Numbers in the reconstruction that were not spoken (as digits or as words) in the raw transcript."""
    said = {n.replace(",", ".") for n in _NUM.findall(raw)}
    low = raw.lower()
    out = []
    for n in _NUM.findall(utt):
        k = n.replace(",", ".")
        if k in said:
            continue
        try:
            words = ru_tts.int_words(int(float(k))) if float(k).is_integer() else ""
        except Exception:
            words = ""
        if words and words.lower() in low:
            continue
        out.append(n)
    return out


def context_line(c) -> str:
    parts = []
    if getattr(c, "vehicle", None):
        v = c.vehicle
        parts.append(f"машина {v.vehicle_id} {v.make or ''} {v.model or ''}, двигатель {v.engine_model}".replace("  ", " "))
    ch = getattr(c, "check", None)
    if ch is not None:
        st = getattr(ch.status, "value", ch.status)
        parts.append(f"проверка {ch.session_id}: {st}" + (f", {ch.anomaly_type} {ch.anomaly_title or ''}" if ch.anomaly_type else ""))
    return "; ".join(parts)


def extract(llm, raw: str, context: str = "", step: str = "") -> dict:
    raw = (raw or "").strip()
    base = {"raw": raw, "utterance": raw, "dropped": [], "confidence": 1.0, "confirm": False, "llm": False}
    if not raw or not needs_llm(raw) or llm is None or not getattr(llm, "available", False):
        return base
    user = (f"Контекст: {context or 'нет'}" + (f"; текущий шаг ремонта: {step}" if step else "") + f"\nРаспознанная запись:\n«{raw[:1500]}»")
    try:
        d = llm.json("voice_intent", SYSTEM, user, max_tokens=400)
    except Exception as e:
        return {**base, "error": type(e).__name__}
    utt = str(d.get("repair_utterance") or "").strip()
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    dropped = [str(x)[:200] for x in (d.get("dropped") or []) if str(x).strip()][:8]
    out = {**base, "llm": True, "dropped": dropped, "confidence": round(conf, 2)}
    if not utt:
        return {**out, "utterance": "", "confidence": 0.0, "confirm": False, "offtopic": True}
    bad = invented_numbers(utt, raw)
    if bad:  # never a number that was not spoken: keep the raw text, ask the mechanic
        return {**out, "utterance": raw, "confidence": min(conf, 0.3), "confirm": True, "invented": bad}
    if not dropped and conf >= CONFIRM_BELOW:
        utt = raw  # clean recording: the mechanic's own words, not a paraphrase
    return {**out, "utterance": utt, "confirm": conf < CONFIRM_BELOW}
