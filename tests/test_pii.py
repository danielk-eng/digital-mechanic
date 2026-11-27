"""Depersonalization before the external LLM (152-FZ): detector, reversible placeholders, streaming, Llm.chat wiring,
and no false positives on technical text (engine models, part numbers, fault codes, norms)."""
import gzip
import json
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from digital_mechanic import pii

ROOT = Path(__file__).resolve().parents[1]

PII_CASES = [
    ("Иванов И.И. просит перезвонить", "Иванов И.И.", "ФИО"),
    ("акт подписал И.И. Иванов", "И.И. Иванов", "ФИО"),
    ("водитель Петров Б. жалуется на стук", "Петров Б.", "ФИО"),
    ("Иванов Иван Иванович, бригада 2", "Иванов Иван Иванович", "ФИО"),
    ("передай Ивану Петровичу", "Ивану Петровичу", "ФИО"),
    ("Борис Петров сменил масло", "Борис Петров", "ФИО"),
    ("Сидорова Анна приняла машину", "Сидорова Анна", "ФИО"),
    ("скажи механику Кузнецову", "Кузнецову", "ФИО"),
    ("Сергей, проверь форсунки", "Сергей", "ФИО"),
    ("Роман Петров на смене", "Роман Петров", "ФИО"),
    ("звони +7 912 345-67-89", "+7 912 345-67-89", "ТЕЛ"),
    ("тел. +7 912 345-67-89. Стучит", "+7 912 345-67-89", "ТЕЛ"),
    ("номер 8 912 345 67 89, жду", "8 912 345 67 89", "ТЕЛ"),
    ("мой номер 8 (912) 345-67-89", "8 (912) 345-67-89", "ТЕЛ"),
    ("номер 8-913-345-67-89", "8-913-345-67-89", "ТЕЛ"),
    ("пиши на 89123456789", "89123456789", "ТЕЛ"),
    ("тел. 912 345 67 89", "912 345 67 89", "ТЕЛ"),
    ("Тел. +7 (4852) 27-46-81", "+7 (4852) 27-46-81", "ТЕЛ"),
    ("брат в Ташкенте: +998 90 123 45 67", "+998 90 123 45 67", "ТЕЛ"),
    ("почта ivanov.ii@mail.ru", "ivanov.ii@mail.ru", "EMAIL"),
    ("машина M333YK47 на яме", "M333YK47", "ГОСНОМЕР"),
    ("гос. номер м851та178", "м851та178", "ГОСНОМЕР"),
    ("номер А 123 ВС 777 RUS", "А 123 ВС 777 RUS", "ГОСНОМЕР"),
    ("O819CY198 в ремонте", "O819CY198", "ГОСНОМЕР"),
    ("VIN XTC65115081234567", "XTC65115081234567", "VIN"),
    ("vin: X89AF71A0D0001234 проверь", "X89AF71A0D0001234", "VIN"),
    ("паспорт 4510 123456", "4510 123456", "ПАСПОРТ"),
    ("паспорт серия 45 10 № 123456", "45 10 № 123456", "ПАСПОРТ"),
    ("серия 4510 номер 123456", "4510 номер 123456", "ПАСПОРТ"),
    ("СНИЛС 112-233-445 95", "112-233-445 95", "СНИЛС"),
    ("снилс 11223344595", "11223344595", "СНИЛС"),
    ("ИНН 7707083893", "7707083893", "ИНН"),
    ("ИНН: 500100732259", "500100732259", "ИНН"),
    ("карта 4276 1600 1234 5678", "4276 1600 1234 5678", "КАРТА"),
]


@pytest.mark.parametrize("text,value,tag", PII_CASES)
def test_masks_personal_data_and_restores(text, value, tag):
    m = pii.Masker()
    out = m.mask(text)
    assert value not in out, out
    assert f"[{tag}_1]" in out, out
    assert m.unmask(out) == text
    if tag in ("ГОСНОМЕР", "VIN"):  # stored form keeps the fleet keys (tickets / history are found by plate)
        assert pii.mask(text) == text
    else:
        assert value not in pii.mask(text) and f"[{tag}]" in pii.mask(text)  # stored / logged form: no numbers


TECH = [
    "ЯМЗ-536, ЯМЗ-5361, ЯМЗ-238М2, ЗМЗ-40904.10, КАМАЗ-65115, КАМАЗ-5490, ПАЗ-3204, ПАЗ-320405-04, МАЗ-5440, ГАЗон NEXT",
    "Код P0101, SPN 3251 FMI 2, ошибка F03 «Стук», DTC U0100, блок EDC7UC31, датчик 0281002930",
    "Момент затяжки 480 Н·м, затем 90°+90°; зазор клапанов 0,25 мм впуск, 0,45 мм выпуск; давление масла 4,5 кгс/см² при 2200 об/мин",
    "Фильтр 840.1012075, 7511.1005132 сталь 40Х, 236-1029122-А, 8421.1109080, 238Н1109080, 7511.1011020-10 СЧ-21 ГОСТ 801-78",
    "Масло М-10Г2к, М-10ДМ, Лукойл Авангард 10W-40, Тосол-А40М, ЦИАТИМ-201, ШРУС-4; Марка масла — по таблице 12",
    "Габариты 7590 823 1320 2300 1400; массы 4795 4685 4685 4775 4840 5110 4885 4950; 536 5361 5362 5363 5364",
    "Напряжение 24 В, ток 80 А, генератор 4001.3771-58, стартер AZJ3381, ТНВД Bosch CP3, форсунка 0445120153",
    "ТО-1 через 15 000 км, ТО-2 через 30 000 км; Common Rail, Euro-5, AdBlue, ЭБУ, ТНВД, МКПП ZF 9S1310",
    "Проверьте длиной 120 мм, Алиной отличается OCR; Лев рычаг; Вера в успех; Роман с ремонтом",
    "Схема 5.2, рис. 54, табл. 7; см. стр. 243; п. 13.2.2; раздел 4.3.1",
    "A1234567890123456 и 12345678901234567 — не VIN (без букв / без цифр): ABCDEFGHJKLMNPRST",
    "Номер двигателя 536.1000186 № 12345; шасси 65115-A4; ресивер 100-3513010",
]


@pytest.mark.parametrize("text", TECH)
def test_technical_text_is_not_masked(text):
    assert pii.find(text) == [], [text[a:b] for a, b, _ in pii.find(text)]


def test_same_value_same_placeholder_and_numbering():
    m = pii.Masker()
    out = m.mask("Петров Б. и Сидоров К.; снова Петров Б.; M333YK47 и м333ук47")
    assert out == "[ФИО_1] и [ФИО_2]; снова [ФИО_1]; [ГОСНОМЕР_1] и [ГОСНОМЕР_1]"


def test_store_mask_keeps_own_display_name_when_asked():
    assert pii.mask("Петров Б.", keep_names=True) == "Петров Б."
    assert pii.mask("Петров Б. +7 912 345-67-89", keep_names=True) == "Петров Б. [ТЕЛ]"


def test_own_placeholders_and_stored_tags_untouched():
    m = pii.Masker()
    assert m.mask("[ФИО_1] и [ТЕЛ] уже скрыты") == "[ФИО_1] и [ТЕЛ] уже скрыты"
    assert m.unmask("[ФИО] из истории") == "[ФИО] из истории"  # stored tag, not this request's placeholder


def test_unmask_tolerates_model_spacing():
    m = pii.Masker()
    m.mask("Петров Б.")
    assert m.unmask("Позвоните [ФИО_1 ] и [ ФИО 1]") == "Позвоните Петров Б. и Петров Б."
    assert m.unmask("[ФИО_7] неизвестен") == "[ФИО_7] неизвестен"


def test_streaming_never_shows_half_a_placeholder():
    m = pii.Masker()
    m.mask("машина M333YK47, водитель Петров Б.")
    full = "Машина [ГОСНОМЕР_1]: [ФИО_1] пусть проверит форсунки [S1]."
    seen = [m.unmask_partial(full[:i]) for i in range(1, len(full) + 1)]
    for s in seen:
        assert "[ГОС" not in s and "[ФИ" not in s and "_1" not in s, s
    assert seen[-1] == "Машина M333YK47: Петров Б. пусть проверит форсунки [S1]."
    assert all(seen[i + 1].startswith(seen[i]) for i in range(len(seen) - 1))  # monotonic: the screen never jumps back
    assert m.unmask_partial("ссылка [S") == "ссылка "  # a held-back «[S» comes back with the next chunk
    assert m.unmask_partial("ссылка [S1]") == "ссылка [S1]"


def test_mask_messages_covers_all_parts():
    m = pii.Masker()
    msgs = [{"role": "system", "content": "Механик: Петров Б., машина M333YK47"},
            {"role": "user", "content": [{"type": "text", "text": "звони +7 912 345-67-89"}, {"type": "image_url", "image_url": {"url": "data:x"}}]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": '{"plate": "M333YK47"}'}}]},
            {"role": "tool", "tool_call_id": "1", "content": '{"owner": "Петров Б."}'}]
    out = m.mask_messages(msgs)
    blob = json.dumps(out, ensure_ascii=False)
    assert "Петров" not in blob and "M333YK47" not in blob and "912" not in blob
    assert msgs[0]["content"].startswith("Механик: Петров")  # the caller's messages are not changed
    assert out[1]["content"][1]["image_url"]["url"] == "data:x"


def _fake_llm(reply_content, tool_args=None, pii_on=True):
    from digital_mechanic.agent.llm import Llm
    from digital_mechanic.config import Settings
    sent = []

    def create(**kw):
        sent.append(kw["messages"])
        tcs = [NS(id="c1", type="function", function=NS(name="find", arguments=tool_args))] if tool_args else None
        return NS(choices=[NS(message=NS(content=reply_content, tool_calls=tcs))], usage=None)
    llm = Llm(Settings(llm_api_key="x", llm_hedge_s=0, llm_stream=False, pii_masking=pii_on))
    llm._client = NS(chat=NS(completions=NS(create=create)))
    return llm, sent


def test_llm_chat_sends_masked_and_returns_restored():
    llm, sent = _fake_llm("Ок, [ФИО_1], машина [ГОСНОМЕР_1] — проверьте ТНВД ЯМЗ-536.", tool_args='{"plate": "[ГОСНОМЕР_1]"}')
    msg, _ = llm.chat("t", [{"role": "system", "content": "Механик Петров Б."}, {"role": "user", "content": "M333YK47 стучит, ЯМЗ-536"}])
    wire = json.dumps(sent, ensure_ascii=False)
    assert "Петров" not in wire and "M333YK47" not in wire and "ЯМЗ-536" in wire
    assert msg.content == "Ок, Петров Б., машина M333YK47 — проверьте ТНВД ЯМЗ-536."
    assert msg.tool_calls[0].function.arguments == '{"plate": "M333YK47"}'  # the tool runs on the real plate


def test_llm_chat_masking_follows_company_switch():
    from digital_mechanic.agent.llm import REQUEST_PII
    llm, sent = _fake_llm("ok")
    tok = REQUEST_PII.set(False)
    try:
        llm.chat("t", [{"role": "user", "content": "Петров Б., M333YK47"}])
    finally:
        REQUEST_PII.reset(tok)
    assert "Петров Б." in json.dumps(sent, ensure_ascii=False)
    llm.chat("t", [{"role": "user", "content": "Петров Б., M333YK47"}])
    assert "Петров" not in json.dumps(sent[-1], ensure_ascii=False)


def test_llm_streamed_chunks_are_restored():
    from digital_mechanic.agent.llm import Llm
    from digital_mechanic.config import Settings
    chunks = ["Машина [ГОС", "НОМЕР_1]: ", "[ФИ", "О_1] ", "пусть проверит."]

    def create(**kw):
        assert "M333YK47" not in json.dumps(kw["messages"], ensure_ascii=False)

        def gen():
            for c in chunks:
                yield NS(usage=None, choices=[NS(delta=NS(content=c, tool_calls=None))])
        return gen()
    llm = Llm(Settings(llm_api_key="x", llm_stream=True))
    llm._client = NS(chat=NS(completions=NS(create=create)))
    shown = []
    msg, _ = llm.chat("t", [{"role": "user", "content": "M333YK47, механик Петров Б."}], on_text=shown.append)
    assert msg.content == "Машина M333YK47: Петров Б. пусть проверит."
    assert shown and all("[ГОС" not in s and "[ФИ" not in s for s in shown)
    assert shown[-1] == msg.content


def test_latency_under_50ms_on_a_large_prompt():
    page = ("Механик Петров Б. (тел. +7 912 345-67-89) на машине M333YK47, VIN XTC65115081234567. " + " ".join(TECH)) * 20
    assert len(page) > 12000
    m = pii.Masker()
    t = time.perf_counter()
    for _ in range(5):
        m.mask(page)
    ms = (time.perf_counter() - t) / 5 * 1000
    assert ms < 50, ms


@pytest.mark.skipif(not (ROOT / "data/index/pages.jsonl.gz").is_file(), reason="manual index not in the public repo")
def test_manual_pages_almost_untouched():
    """4 147 manual pages: only real names (authors), service e-mails and phones; no masses / part numbers / codes."""
    hits, n = {}, 0
    for f in ("pages.jsonl.gz", "extra_pages.jsonl.gz"):
        p = ROOT / "data/index" / f
        if not p.is_file():
            continue
        for line in gzip.open(p, "rt", encoding="utf-8"):
            n += 1
            for _, _, k in pii.find(json.loads(line).get("text") or ""):
                hits[k] = hits.get(k, 0) + 1
    assert n > 1000
    assert hits.get("plate", 0) == 0 and hits.get("vin", 0) == 0 and hits.get("passport", 0) == 0
    assert hits.get("phone", 0) <= 5 and hits.get("card", 0) <= 3, hits
