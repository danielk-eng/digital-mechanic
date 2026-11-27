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
model = "DeepSeek V4.1 Flash (deepseek-v4.1-flash)"
usd_in, usd_out = calc.PRICES["primary_paid"]["usd"]


def agg(keys):
    calls = sum(rows[k]["calls"] for k in keys)
    tin = sum(rows[k]["calls"] * rows[k]["in"] for k in keys) / calls if calls else 0
    tout = sum(rows[k]["calls"] * rows[k]["out"] for k in keys) / calls if calls else 0
    return round(calls, 2), round(tin), round(tout)


lines = [("Объяснение вердикта и вопросы механика: выбор инструмента + поиск + ответ, с повторными вызовами", ["explain", "answer", "retry"]),
         ("«Подробнее» — развёрнутый ответ (готовится в фоне)", ["expand"]),
         ("Выжимки цитат руководства (по нажатию на страницу)", ["extract"]),
         ("Черновик заявки в ТОиР (работы с дословными цитатами)", ["ticket"])]
for i, (name, keys) in enumerate(lines):
    n = 9 + i
    c, tin, tout = agg(keys)
    ws[f"A{n}"], ws[f"B{n}"], ws[f"C{n}"], ws[f"D{n}"], ws[f"E{n}"] = name, model, c, tin, tout
    ws[f"F{n}"], ws[f"G{n}"], ws[f"H{n}"] = usd_in, usd_out, "$"
m = r["measured"]
f50 = next(f for f in r["fleets"] if f["vehicles"] == 50)
ws["A17"] = (f"1 запрос = сценарий от результата диагностики до ответа/заявки: {m['scenario']}; все шаги модели суммированы; эмбеддинги запроса = 0 (поиск BM25 на сервере). "
             f"Токены — замер на стенде {m['measured_at']} CST ({m['runs']} прогона). Цена — платный прайс DeepSeek API (пик); на стенде модель бесплатная (TokenRouter free) = 0 ₽, резерв GigaChat-4 на Cloud.ru тоже 0 ₽.")
ws["A18"] = (f"Парк 50 ТС, проверка раз в 14 дней, 2–3 вопроса на проверку, заявка в 35 % проверок: модели ≈ {f50['models_primary_paid']:.0f} ₽/мес (платно) или 0 ₽ (бесплатно); "
             f"сервер Cloud.ru 2 vCPU/4 ГБ {f50['server']:.0f} ₽/мес; сопровождение {f50['support_hours']} ч × 2 000 ₽. Разовая индексация руководств: BM25 0 ₽ (bge-m3 был бы ≈ {r['indexing']['bge_m3_rub']:.2f} ₽).")
ws["A19"] = ("Что меняет цифры: сложный ремонт до ~50 обменов (≈ ×" + f"{r['complex_repair']['primary_paid'] / r['total']['primary_paid']:.0f}" + " к сценарию), платный резерв GigaChat Ultra (78,87/236,56 ₽ за 1 млн), локальная модель на GPU (токены 0, сервер с GPU). "
             "Подробно: лист «Разбивка и парк», страница /calculator.")
# detail sheet
d = wb.create_sheet("Разбивка и парк")
d.append(["Шаг", "Вызовов", "Вход/вызов", "Выход/вызов"] + [calc.PRICES[k]["name"] for k in calc.PRICES])
for x in r["rows"]:
    d.append([x["name"], x["calls"], x["in"], x["out"]] + [round(x[k], 4) for k in calc.PRICES])
t = r["total"]
d.append(["Итого на 1 сценарий", round(t["calls"], 2), round(t["in"]), round(t["out"])] + [round(t[k], 4) for k in calc.PRICES])
d.append([])
d.append(["Сложный ремонт (~50 обменов)", round(r["complex_repair"]["calls"], 1), round(r["complex_repair"]["in"]), round(r["complex_repair"]["out"])] + [round(r["complex_repair"][k], 2) for k in calc.PRICES])
d.append(["Разовая индексация руководств, токенов", r["indexing"]["tokens"], "BM25: 0 ₽", f"bge-m3: {r['indexing']['bge_m3_rub']:.2f} ₽"])
d.append([])
d.append(["Машин", "Проверок/мес", "Модели бесплатно", "Модели платный аналог", "Модели резерв платно", "Сервер", "Сопровождение", "Итого (бесплатно)", "Итого (платный аналог)", "Итого (резерв платно)"])
for f in r["fleets"]:
    d.append([f["vehicles"], round(f["checks_month"], 1), round(f["models_primary_free"]), round(f["models_primary_paid"]), round(f["models_fallback_paid"]),
              round(f["server"]), round(f["support"]), round(f["total_primary_free"]), round(f["total_primary_paid"]), round(f["total_fallback_paid"])])
a = r["assumptions"]
d.append([])
d.append([f"Допущения: проверка раз в {a['check_every_days']} дней; {a['questions_per_check']} вопроса на проверку; заявка в {int(a['ticket_share'] * 100)} % проверок; "
          f"сервер {a['server_rub_month']} ₽/мес (2 шт. для 800 ТС); сопровождение {a['support_rate_rub_h']} ₽/ч; курс ЦБ {calc.USD_RUB}; суммы без НДС."])
for c in d[1]:
    c.font = Font(bold=True)
d.column_dimensions["A"].width = 60
wb.save(out)
print("saved", out)
