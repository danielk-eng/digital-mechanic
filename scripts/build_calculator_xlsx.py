"""Fill the organizers' calculator template from cost/calc.py -> src/digital_mechanic/web/static/calculator.xlsx
(served without login at /static/calculator.xlsx, linked from /calculator). Needs openpyxl.

    python3 scripts/build_calculator_xlsx.py "/path/ИИкузница — калькулятор экономики решения.xlsx"
"""
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from digital_mechanic.cost import calc  # noqa: E402

tpl = sys.argv[1]
out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "src/digital_mechanic/web/static/calculator.xlsx"
r = calc.report()
rows = {x["step"]: x for x in r["rows"]}
wb = openpyxl.load_workbook(tpl)
ws = wb["Расчёт"]
ws["I3"] = calc.USD_RUB
model = "DeepSeek-V4.1-flash-1017 (SCX.ai)"
P = calc.PRICES["primary_paid"]
usd_in, usd_out = P["usd"]
usd_cached = P["usd_cached"]


def agg(keys):
    calls = sum(rows[k]["calls"] for k in keys)
    tin = sum(rows[k]["calls"] * rows[k]["in"] for k in keys) / calls if calls else 0
    tout = sum(rows[k]["calls"] * rows[k]["out"] for k in keys) / calls if calls else 0
    tok = sum(rows[k]["calls"] * rows[k]["in"] for k in keys)
    share = sum(rows[k]["calls"] * rows[k]["in"] * rows[k].get("cached_share", 0.0) for k in keys) / tok if tok else 0
    return round(calls, 2), round(tin), round(tout), share


lines = [("Объяснение вердикта и вопросы механика: выбор инструмента + поиск + ответ, с повторными вызовами", ["explain", "answer", "retry"]),
         ("«Подробнее» — развёрнутый ответ (готовится в фоне)", ["expand"]),
         ("Выжимки цитат руководства (по нажатию на страницу)", ["extract"]),
         ("Черновик заявки в ТОиР (работы с дословными цитатами)", ["ticket"])]
for i, (name, keys) in enumerate(lines):
    n = 9 + i
    c, tin, tout, share = agg(keys)
    ws[f"A{n}"], ws[f"B{n}"], ws[f"C{n}"], ws[f"D{n}"], ws[f"E{n}"] = name, model, c, tin, tout
    # effective input price: cached share of the input billed at the cached rate (SCX.ai)
    ws[f"F{n}"], ws[f"G{n}"], ws[f"H{n}"] = round(share * usd_cached + (1 - share) * usd_in, 4), usd_out, "$"
m = r["measured"]
f50 = next(f for f in r["fleets"] if f["vehicles"] == 50)
cache = m["cache"]
ws["A17"] = (f"1 запрос = сценарий от результата диагностики до ответа/заявки: {m['scenario']}; все шаги модели суммированы; эмбеддинги запроса = 0 (поиск BM25 на сервере). "
             f"Токены — замер на стенде {m['measured_at']} CST ({m['runs']} прогона). Цена — прайс SCX.ai {calc.SCX_URL}: $0,30 вход, $0,03 вход из кэша, $1,20 выход за 1 млн; "
             f"вход в столбце F — смешанная цена по доле кэша; в приложенном замере кэш не отмечен отдельно, поэтому консервативно взята полная цена входа. На стенде модель бесплатная (TokenRouter free) = 0 ₽.")
ws["A18"] = (f"Парк 50 ТС, проверка раз в 14 дней, 2–3 вопроса на проверку, заявка в 35 % проверок: модели ≈ {f50['models_primary_paid']:.0f} ₽/мес (SCX.ai) или 0 ₽ (стенд); "
             f"сервер Cloud.ru 2 vCPU/4 ГБ {f50['server']:.0f} ₽/мес; сопровождение {f50['support_hours']} ч × 2 000 ₽. Голос Cloud.ru SaluteSpeech — опция: "
             f"≈ {r['voice']['total_rub']:.2f} ₽ за полностью голосовой сценарий, верхний предел {f50['voice_month']:.0f} ₽/мес, если все проверки голосом. "
             f"Разовая индексация руководств: BM25 0 ₽ (bge-m3 был бы ≈ {r['indexing']['bge_m3_rub']:.2f} ₽).")
ws["A19"] = ("Что меняет цифры: сложный ремонт до ~50 обменов (≈ ×" + f"{r['complex_repair']['primary_paid'] / r['total']['primary_paid']:.0f}" + " к сценарию), доля кэша (без кэша вход $0,30), платный резерв GigaChat Ultra (78,87/236,56 ₽ за 1 млн), "
             "доля голосовых проверок (текстовый сценарий не требует speech-API). Подробно: листы «Разбивка и парк» и «Голос SaluteSpeech», страница /calculator.")
# detail sheet
d = wb.create_sheet("Разбивка и парк")
d.append(["Шаг", "Вызовов", "Вход/вызов", "Выход/вызов", "Доля входа из кэша"] + [calc.PRICES[k]["name"] for k in calc.PRICES])
for x in r["rows"]:
    d.append([x["name"], x["calls"], x["in"], x["out"], round(x.get("cached_share", 0.0), 3)] + [round(x[k], 4) for k in calc.PRICES])
t = r["total"]
d.append(["Итого на 1 сценарий", round(t["calls"], 2), round(t["in"]), round(t["out"]), round(cache["share_provider"], 3)] + [round(t[k], 4) for k in calc.PRICES])
d.append([])
d.append(["Сложный ремонт (~50 обменов)", round(r["complex_repair"]["calls"], 1), round(r["complex_repair"]["in"]), round(r["complex_repair"]["out"]), ""] + [round(r["complex_repair"][k], 2) for k in calc.PRICES])
d.append(["Разовая индексация руководств, токенов", r["indexing"]["tokens"], "BM25: 0 ₽", f"bge-m3: {r['indexing']['bge_m3_rub']:.2f} ₽"])
d.append([])
d.append(["Машин", "Проверок/мес", "Модели бесплатно", "Модели SCX.ai (платно)", "Модели резерв платно", "Голос, если все проверки голосом", "Сервер", "Сопровождение", "Итого (бесплатно)", "Итого (SCX.ai)", "Итого (резерв платно)"])
for f in r["fleets"]:
    d.append([f["vehicles"], round(f["checks_month"], 1), round(f["models_primary_free"]), round(f["models_primary_paid"]), round(f["models_fallback_paid"]),
              round(f["voice_month"]), round(f["server"]), round(f["support"]), round(f["total_primary_free"]), round(f["total_primary_paid"]), round(f["total_fallback_paid"])])
a = r["assumptions"]
d.append([])
d.append([f"Допущения: проверка раз в {a['check_every_days']} дней; {a['questions_per_check']} вопроса на проверку; заявка в {int(a['ticket_share'] * 100)} % проверок; "
          f"сервер {a['server_rub_month']} ₽/мес (2 шт. для 800 ТС); сопровождение {a['support_rate_rub_h']} ₽/ч; курс ЦБ {calc.USD_RUB}; суммы без НДС. "
          f"Цена SCX.ai: {calc.SCX_URL}; кэш в приложенном замере не отмечен отдельно — расчёт консервативно использует полную цену входа."])
d.append([])
v = r["voice"]
d.append(["Голосовой сценарий — Cloud.ru SaluteSpeech (опция)", "Объём", "Тариф без НДС", "₽/сценарий"])
d.append(["Распознавание речи", f"{v['stt_seconds_per_scenario']:.0f} сек", f"{v['stt_rub_second']:.8f} ₽/сек", round(v["stt_rub"], 4)])
d.append(["Синтез речи", f"{v['tts_chars_per_scenario']:.0f} знаков", f"{v['tts_rub_per_1000_chars']:.2f} ₽/1000 знаков", round(v["tts_rub"], 4)])
d.append(["Итого голосовой сценарий", "", "", round(v["total_rub"], 4)])

# A separate voice sheet keeps the organizers' first sheet untouched while making the speech tariff auditable.
if "Голос SaluteSpeech" in wb.sheetnames:
    del wb["Голос SaluteSpeech"]
g = wb.create_sheet("Голос SaluteSpeech")
v = r["voice"]
g.append(["Голосовой сценарий — Cloud.ru SaluteSpeech"])
g.append(["Опция к текстовому сценарию: если механик диктует вопросы и слушает ответы. Суммы без НДС, тариф Cloud.ru Foundation Models 261126."])
g.append([])
g.append(["Допущение", "Значение"])
g.append(["Входная речь на сценарий, сек", v["stt_seconds_per_scenario"]])
g.append(["Озвучиваемый текст на сценарий, знаков", v["tts_chars_per_scenario"]])
g.append([])
g.append(["Строка", "Объём", "Тариф", "₽/сценарий"])
g.append(["Распознавание речи SaluteSpeech", v["stt_seconds_per_scenario"], v["stt_rub_second"], "=B9*C9"])
g.append(["Синтез речи SaluteSpeech", v["tts_chars_per_scenario"], v["tts_rub_per_1000_chars"], "=B10/1000*C10"])
g.append(["Итого голосовой сценарий", "", "", "=SUM(D9:D10)"])
g.append([])
g.append(["Машин", "Проверок/мес", "Верхний предел голоса/мес, если все проверки голосом"])
for f in r["fleets"]:
    g.append([f["vehicles"], round(f["checks_month"], 1), round(f["voice_month"], 2)])
g.append([])
g.append(["Если голосом проходит только часть проверок, строка масштабируется линейно: например, 30 % голосовых проверок = 30 % суммы."])
g.append(["В текстовом сценарии speech-API не используется и стоит 0 ₽. Локальный резерв на стенде — Silero v5_cis_base_nostress (MIT), без облачной платы."])
for c in g[1]:
    c.font = Font(bold=True)
for c in g[8]:
    c.font = Font(bold=True)
for c in g[13]:
    c.font = Font(bold=True)
g.column_dimensions["A"].width = 58
g.column_dimensions["B"].width = 18
g.column_dimensions["C"].width = 18
g.column_dimensions["D"].width = 18

for c in d[1]:
    c.font = Font(bold=True)
d.column_dimensions["A"].width = 60
wb.save(out)
print("saved", out)
