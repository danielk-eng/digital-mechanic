# Цифровой механик (ИИкузница Softline 2026)

Веб-чат для механика (работает на смартфоне): выбор машины → результат акустической проверки из ML-сервиса →
детерминированное решение (повтор записи / норма / отклонение) → поиск **только** в руководствах двигателя этой
машины со страницами PDF → состав заявки → отправка в ТОиР **только после подтверждения** механика.

## Быстрый старт (Linux / macOS)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export LLM_API_KEY=...        # ключ OpenAI-совместимого API (в репозиторий не кладём)
# по умолчанию: https://api.tokenrouter.com/v1, модель deepseek/deepseek-v4.1-flash-1017-free
scripts/run_demo.sh           # заглушки ML (8101) и ТОиР (8102) + веб (8000)
# открыть http://localhost:8000 (со смартфона — http://<ip>:8000)
```
Windows 11 (PowerShell, Python 3.10+):
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt
$env:PYTHONPATH="$PWD\src"; $env:LLM_API_KEY="..."
Start-Process uvicorn "digital_mechanic.stubs.ml_stub:app --port 8101"
Start-Process uvicorn "digital_mechanic.stubs.toir_stub:app --port 8102"
uvicorn digital_mechanic.demo.app:app --port 8000
```
Без ключа LLM агент работает в деградированном режиме (извлекающий разбор «Работы/Запчасти», ответы — страницы руководства).

## Официальные данные
Каталог `data/mock/` — **синтетические dev-фикстуры** (помечены MOCK), не данные организаторов.
Подмена на официальные: переменные окружения `VEHICLES_PATH`, `MANUALS_DIR`, `ENGINE_DOCS_PATH` (названия `document` — как в разделе 3 ТЗ),
`ML_SERVICE_URL`, `TOIR_SERVICE_URL` (официальные заглушки вместо наших моков). Имена полей API — только в `src/digital_mechanic/adapters.py`.

## Как устроено
| Модуль | Роль |
|---|---|
| `policy.py` | Детерминированная политика: сбой/плохое качество → повтор записи; норма → без заявки; отклонение → заявка по подтверждению; нет опоры в книге → дефектовка без заявки |
| `priority.py` | Приоритет low/medium/high из оценки отклонения, уверенности, критичности узла, качества записи; основание показывается механику |
| `ingest/pages.py` | Постраничное извлечение PDF (текстовый слой; страницы-картинки помечаются `needs_ocr`, OCR — опционально) |
| `retrieval/` | BM25 по страницам, жёсткий фильтр по двигателю **до** ранжирования |
| `agent/grounding.py` | Проверка: каждая работа/запчасть/цитата должна дословно присутствовать на найденной странице этого двигателя, иначе отбрасывается |
| `agent/orchestrator.py` | План → инструменты → проверка → ответ; ворота подтверждения; перед POST повторная валидация (двигатель, документ, страница, цитата) |
| `demo/` | FastAPI + мобильный веб-чат, ссылки на страницы PDF |

Трасса хода (JSON без секретов): `runs/traces/<sid>.json`, также `GET /api/trace/<sid>`. Расход токенов: `GET /api/usage`.
Кэш одинаковых запросов к LLM (temperature 0) — `LLM_CACHE_DIR`, только ускорение повторных прогонов.

## Тесты
```bash
PYTHONPATH=src python -m pytest -q
```
## Лицензии
pypdf (BSD), FastAPI, httpx, pydantic, openai-клиент (MIT/Apache). AGPL-компонентов нет.
