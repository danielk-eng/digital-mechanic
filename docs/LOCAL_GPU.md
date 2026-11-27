# Локальный режим на GPU инженера: RTX 3090 и DGX Spark

Цель — «Цифровой механик» без облака: вопрос, голос и ответ не покидают площадку (152-ФЗ, закрытый контур предприятия). Код приложения уже работает с любым OpenAI-совместимым сервером (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`), поэтому локальный режим — это конфигурация, а не форк: `docker-compose.local-gpu.yml` + `local-gpu.env.example`.

Обозначения: **[замер]** — опубликованный бенчмарк со ссылкой; **[оценка]** — расчёт по формуле ниже или экстраполяция, проверить `scripts/bench_local.py` на железе. Выдуманных чисел нет: всё, что не замер, помечено как оценка.

## 1. Железо

| | RTX 3090 | DGX Spark (GB10) |
|---|---|---|
| Память | 24 GB GDDR6X | 128 GB LPDDR5x, общая с CPU |
| Пропускная способность памяти | 936 GB/s | ~273 GB/s |
| Арх. / форматы | Ampere sm_86: FP16/INT8/INT4, **нет FP8/FP4 в железе** | Blackwell sm_121: FP8, NVFP4 (~1 PFLOP FP4 sparse) |
| Вывод | быстрее в 3,4 раза на генерации, но мало памяти | медленнее на генерации, зато всё помещается одновременно |

## 2. Наша нагрузка (из логов стенда)

- Один ход чата = 2–3 вызова LLM (`facts` → `render`, иногда `agent_r*` с инструментами). По `var/app.log.jsonl` (623 хода): токенов на ход **медиана 2 375, p90 ≈ 10 000** (вход + выход).
- Выход ≈ **900 токенов на ход** (медиана из `audit/MODEL_BAKEOFF.md`), вход — фрагменты руководств (RAG): **1,5k типично, 8–12k тяжёлый ход**.
- Нужны: tool calling (агентные раунды), стриминг, русский язык. Поиск — BM25 (hit@1 0,94), эмбеддинги и реранкер **не обязательны** (реранкеры ухудшили результат, см. bake-off).

## 3. Формулы

**Генерация (decode) упирается в память:** на каждый токен читаются активные веса + KV-кэш контекста.

```
tok/s ≈ η · BW / (P_active · bpw/8 + KV_bytes_per_token · L_ctx)
```
`BW` — пропускная способность, `P_active` — активные параметры (для MoE только выбранные эксперты), `bpw` — бит на вес квантования, `L_ctx` — текущая длина контекста, `η` — КПД движка (по замерам 0,4–0,8).

**KV-кэш на токен:** `2 (K,V) · слои · kv_heads · head_dim · байт`. Для MLA (GigaChat3): `слои · (kv_lora_rank + rope_dim) · байт`.

**Время до первого токена (TTFT)** упирается в вычисления: `TTFT ≈ L_prompt / prefill_rate`.

**Время хода [оценка]:** `T ≈ L_prompt / prefill + 900 / decode` (2 вызова суммарно; стриминг показывает первые строки раньше).

### KV-кэш (из `config.json` моделей)

| Модель | Слои / KV-головы / head_dim | KV на токен (fp16) | 12k токенов, 1 пользователь |
|---|---|---|---|
| Qwen3-30B-A3B-Instruct-2507 | 48 / 4 / 128 | 96 КиБ (48 КиБ при q8_0) | 1,18 ГБ (0,59 ГБ q8) |
| Qwen3-32B (dense) | 64 / 8 / 128 | 256 КиБ | 3,1 ГБ |
| gpt-oss-20b | 24 (12 full + 12 SWA-128) / 8 / 64 | ≈24 КиБ (SWA-слои держат 128 токенов) | 0,3 ГБ |
| gpt-oss-120b | 36 (18 full + 18 SWA-128) / 8 / 64 | ≈36 КиБ | 0,45 ГБ |
| GigaChat3-10B-A1.8B | 26, MLA (512 + 64) | ≈29 КиБ | 0,36 ГБ |

### Потолок генерации по формуле и реальные замеры

| Модель, квант | Байт/токен (веса) | 3090 потолок → **замер** | Spark потолок → **замер** |
|---|---|---|---|
| Qwen3-30B-A3B Q4_K_M (4,87 bpw, 3,3B акт.) | ~2,0 ГБ | 468 → **187–200** [1][2] | 136 → **77–85** (vLLM 4-bit / Ollama Q4) [5][6] |
| Qwen3-30B-A3B Q8_0 / FP8 | ~3,5 ГБ | не влезает (30 ГБ) | 78 → **61** (d0), **48** при контексте 8k [4]; FP8 vLLM **50–55** [6] |
| gpt-oss-20b MXFP4 (3,6B акт.) | ~2,1 ГБ | 450 → **200–211** [1][2] | 131 → **83**, **75** при 8k [4] |
| gpt-oss-120b MXFP4 (5,1B акт.) | ~2,8 ГБ | не влезает (59 ГБ) | 99 → **59**, **53** при 8k [4]; vLLM **61** [5] |
| Qwen3-32B AWQ (dense) | ~19 ГБ | 49 → **~39** (класс 27–32B, llama.cpp Q4) [2] | 14 → ~10 [оценка; Llama-3.3-70B на Spark — 6 tok/s [6]] |
| GigaChat3-10B-A1.8B FP8 (1,8B акт.) | ~1,8 ГБ | 520 → ~200–250 [оценка, η 0,4–0,5] | 150 → ~100 [оценка] |

КПД η по замерам: 3090 + llama.cpp ≈ 0,42; Spark ≈ 0,6–0,8. Падение скорости при контексте 8k на Spark (61 → 48) — это чтение KV (≈0,8 ГБ на токен при 8k для Qwen3-30B), формула его предсказывает.

**Prefill (TTFT) [замер]:** 3090 Qwen3-30B-A3B Q4 — 3 800–4 000 tok/s [1][2] → 10k токенов ≈ **2,5 с**; Spark: gpt-oss-20b ~4 000 tok/s при 8k [4], gpt-oss-120b ~2 200 [4], Qwen3-30B Q8 ~2 350 [4], Qwen3-30B AWQ vLLM 5 705 на 8k-промпте [6] → 10k ≈ **1,8–4,5 с**.

## 4. Модели по задачам (только лицензии, допускающие коммерцию в РФ)

| Задача | Модель | Лицензия | Память | Скорость | Примечание |
|---|---|---|---|---|---|
| LLM, основная | **Qwen3-30B-A3B-Instruct-2507** | Apache-2.0 | Q4_K_M 17,3 ГиБ / FP8 ~31 ГБ / NVFP4 ~17 ГБ | см. §3 | без «рассуждения» (не тратит токены), tool calling, 256k контекст |
| LLM, быстрая/лёгкая | gpt-oss-20b | Apache-2.0 | 11,3 ГиБ | 3090: 200+ tok/s [1] | всегда рассуждает (low/medium/high) → лишние токены; русский слабее [оценка] |
| LLM, качество (только Spark) | gpt-oss-120b | Apache-2.0 | 59 ГиБ | 53–61 tok/s [4][5] | сильнейшая открытая модель, что влезает; рассуждение добавляет токены |
| LLM, российская | GigaChat3-10B-A1.8B (Lightning) | MIT | FP8 12,3 ГБ / bf16 23 ГБ | [оценка] ~200+ / ~100 tok/s | MMLU-RU 0,68 против 0,60 у Qwen3-4B [8], но мала для цитирования по 10k контекста; кандидат для лёгких шагов (стиль, подсказки) |
| ASR ru | **GigaAM-v3 e2e_rnnt** | MIT | ~240M, <1 ГБ fp16 | реальное время ≪ 1 [оценка] | WER 8,4 % в среднем против 25,1 % у Whisper [9]; пунктуация и нормализация из коробки |
| ASR ru (стриминг) | T-one (Т-Банк) | Apache-2.0 [10] | ~70M | потоковая | для телефонии; GigaAM-v3 точнее (19,4 % против 8,4 % WER) [9] |
| ASR kk / uz | **GigaAM-Multilingual-v2** (rnnt) | MIT | 776M, ~1,6 ГБ fp16 | [оценка] < 0,5 с на фразу | WER: kk 3,95 %, uz 3,70 % против 35,8 % и 104,8 % у Whisper-large-v3 [11]. Закрывает нашу дыру с узбекским (у нас navai 0,17, облачный whisper 0,96) |
| ASR hi / vi (и запасной) | **whisper-large-v3-turbo** (faster-whisper, CTranslate2) | MIT | ~1,6 ГБ fp16 / ~0,8 ГБ int8 | замеры faster-whisper [12] | на наших фразах hi/vi WER 0 (cloud whisper-large-v3) |
| TTS ru / kk / uz | **Silero v5_cis_base(_nostress)** | **MIT** [13] | CPU | быстрый на CPU | ru-голоса с префиксом `ru_`; ударения — разметкой `+` |
| TTS hi / vi | браузерный TTS (как сейчас) | — | — | — | открытых TTS с коммерческой лицензией для hi/vi с нужным качеством не нашли |
| Эмбеддинги (опц.) | bge-m3 | MIT | 568M, ~1,1 ГБ | — | гибрид BM25+dense не дал прироста на 18 вопросах (bake-off) |
| Реранкер (опц.) | bge-reranker-v2-m3 | Apache-2.0 | 568M | — | ухудшил hit@1 (0,94 → 0,83) — не включаем |

**Важно про лицензии.** Silero `v4_ru`/`v5_*_ru` (сейчас в `services/speech`) — **CC BY-NC-SA, коммерция запрещена** [13]. Для продукта нужно перейти на `v5_cis_base_nostress` (MIT) — сделано в `services/speech_gpu`. XTTS-v2 (Coqui Public Model License) и F5-TTS (веса CC-BY-NC) **не подходят** для коммерции.

## 5. Сводная таблица: GPU → модели → скорость → влезает ли

Ход чата: «типичный» = 1,5k вход + 900 выход, «тяжёлый» = 10k вход + 900 выход. Текущая облачная модель: медиана 11,4 с.

| GPU | Конфиг | Память | Генерация, 1 польз. | Ход типичный / тяжёлый | Одновременно | Всё на одной карте? |
|---|---|---|---|---|---|---|
| **RTX 3090** | **Qwen3-30B-A3B-Instruct-2507 Q4_K_M** (llama.cpp, 3 слота × 12k, KV q8) + GigaAM-v3 + GigaAM-Multilingual-v2 на GPU; whisper-turbo и Silero на CPU | 17,3 + 1,7 KV + ~1 буферы + ~2,8 ASR ≈ **22,8 из 24 ГиБ** [оценка] | **187–200 tok/s** [замер 1,2] | **~5 с / ~7,5 с** [оценка] | 3 ответа параллельно; агрегат 10 потоков 372 tok/s [замер 1] | **Да, впритык** (без рабочего стола на этой карте) |
| RTX 3090 | gpt-oss-20b MXFP4 + все ASR на GPU | 11,3 + ~0,5 KV + ~4 ASR ≈ 16 ГиБ | 200–211 tok/s [замер 1,2] | ~5 / ~7 с + рассуждение [оценка] | 6–8 | Да, с запасом |
| RTX 3090 | Qwen3-32B AWQ (dense) | ~19 + 3,1 KV на 1 польз. | ~39 tok/s [замер 2, класс 27–32B] | ~25 / ~28 с | 1 | Только LLM, ASR — на CPU. **Не рекомендуем** |
| **DGX Spark** | **Qwen3-30B-A3B-Instruct-2507 FP8** (vLLM) или NVFP4 + все ASR + Silero + bge-m3 | 31 (FP8) + KV + ~4 ASR ≈ **40 ГБ из 128** | FP8 **50–55**, 4-bit **77–81** [замер 6] | FP8 ~17 / ~22 с; 4-bit ~12 / ~17 с [оценка] | 8: агрегат 313 tok/s (≈39 на польз.) [замер 5] | **Да, с большим запасом** |
| DGX Spark | + gpt-oss-120b (режим «качество», второй сервер) | +59 ГиБ → ~100 ГБ | 53–61 tok/s [замер 4,5] | ~16 / ~22 с + рассуждение [оценка] | 8: агрегат 116–153 tok/s [замер 5] | Да (вместе с Qwen3-30B) |
| DGX Spark | GigaChat3-10B-A1.8B bf16 | 23 ГБ | ~100 tok/s [оценка] | ~9 / ~13 с [оценка] | 16+ | Да |

Вывод: **по скорости ответа 3090 выигрывает** (в 2–3 раза быстрее Spark на генерации — пропускная способность 936 против 273 GB/s). **Spark выигрывает по ёмкости**: держит FP8 без потерь от 4-битного кванта, все речевые модели и большую модель для «Подробнее» одновременно и обслуживает больше пользователей.

Сколько механиков обслужит [оценка]: при одном вопросе в 3 минуты и занятости GPU ~7 с на ход — 3090 ≈ 20–25 активных механиков, Spark ≈ 40–60 (за счёт батчинга vLLM).

## 6. Качество: ожидаемая дельта к текущей модели

Текущая планка: **cite_acc 0,944** (17 из 18), cite_prec 0,89, refuse_acc 0,75 — deepseek-v4.1-flash через TokenRouter. GigaChat-4-708B на том же наборе дал 0,889.

- Цитата в нашем конвейере определяется в первую очередь поиском (BM25, hit@5 = 1,00) и шагом «скопируй факты из фрагментов» (температура 0). Поэтому разрыв моделей на цитатах меньше, чем на общих бенчмарках.
- Qwen3-30B-A3B-Instruct-2507 по карточке [7]: IFEval 84,7 против 82,3 у DeepSeek-V3-0324, BFCL-v3 (tool calling) 65,1 против 64,7, но многоязычные знания ниже (INCLUDE 71,9 против 80,1; MMLU-ProX 72,0 против 75,8).
- **Ожидание [оценка]: cite_acc 0,83–0,94** (0–2 вопроса из 18 хуже), стиль на русском — заметно проще GigaChat. 1 вопрос = 0,056, т. е. в пределах шума набора.
- gpt-oss-120b [оценка]: цитаты на уровне текущей модели, но +рассуждение и медленнее. GigaChat3-10B [оценка]: −2…−4 вопроса, только для лёгких шагов.
- Решение принимать по замеру: `scripts/bench_local.py --app ... --qset eval/qset.json` (тот же набор и та же метрика, что в `audit/METRICS.md`).

## 7. Рекомендация

**RTX 3090 (одна карта, «механик-сервер» в цеху):**
- LLM: `unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:Q4_K_M` в `llama.cpp server` (`--jinja`, 3 слота × 12k, KV q8_0, flash-attn).
- ASR: GigaAM-v3 (ru) + GigaAM-Multilingual-v2 (kk, uz) на GPU; whisper-large-v3-turbo int8 на CPU (hi, vi).
- TTS: Silero v5_cis_base_nostress на CPU (MIT); hi/vi — браузер.
- Приложение: `LLM_BASE_URL=http://llm:8080/v1`, хеджирование запросов выключено (на своей карте дубль запроса только удваивает нагрузку).

**DGX Spark (сервер предприятия / филиала):**
- LLM: `Qwen/Qwen3-30B-A3B-Instruct-2507-FP8` в vLLM (официальный контейнер NVIDIA для Spark); после проверки — NVFP4-версия (+40–50 % скорости, нужен vLLM с нативными FP4-ядрами для sm_121, иначе падение до ~1 tok/s [5]).
- Опционально второй сервер gpt-oss-120b для «Подробнее» и отчётов, где скорость не критична.
- ASR/TTS — как на 3090, всё на GPU (whisper на ARM-хосте — CPU, у Grace 20 ядер).

**Гибрид (рекомендуемый режим продукта):**
- Локальная LLM — основная для всех запросов с персональными данными (а с маскированием ПДн — для всех).
- Облако — только резерв при перегрузке или падении локального сервера: GigaChat-4 на Cloud.ru (серверы в РФ) с уже включённым маскированием ПДн (`PII_MASKING=true`: ФИО, телефоны, госномера, VIN заменяются метками до отправки).
- Настройка: `LLM_PRIMARY=tokenrouter` (это «OpenAI-совместимый слот», он указывает на локальный сервер) + `CLOUDRU_API_KEY` для резерва. Строгий режим — без облачных ключей вовсе.
- Распознавание речи: локально первым (`STT_ORDER=local-first`), облако — резерв.

## 8. Как запустить и замерить

```bash
cp local-gpu.env.example .env          # выбрать профиль и модель
docker compose -f docker-compose.yml -f docker-compose.local-gpu.yml --profile gpu3090 up -d   # или --profile spark
python scripts/bench_local.py llm --base http://localhost:8080/v1 --model local-llm --prompt-tokens 10000 --concurrency 1,3,8
python scripts/bench_local.py app --app http://localhost:8000 --qset eval/qset.json   # cite_acc / cite_prec / refuse_acc
python scripts/bench_local.py speech --speech http://localhost:9000 --audio-dir audit/asr
```
Результаты пишутся в `runs/bench_local_*.json`; таблицы §3 и §5 обновить фактическими числами.

## 9. Ограничения и что осталось

- На реальных 3090 и Spark конфигурация **ещё не запускалась**: числа в §5 — замеры чужих стендов и оценки. Первый запуск — `bench_local.py`, затем обновить таблицы.
- Отдельного значения `LLM_PRIMARY=local` в коде пока нет: локальный сервер занимает OpenAI-совместимый слот (`LLM_PRIMARY=tokenrouter`), облачный резерв — слот Cloud.ru. Это работает без изменений кода. Псевдоним `local` (одна строка в `agent/llm.py` и список в админке) добавить после освобождения `WORKER.lock`.
- Русская речь в Silero `v5_cis_base_nostress` требует ударений (`+`) в тексте; без них интонация хуже, чем у текущей некоммерческой `v4_ru`. Нужен расстановщик ударений с коммерческой лицензией или браузерный TTS по умолчанию.
- Фото узлов (vision) в локальном режиме: на 3090 места нет; на Spark — отдельный сервер Qwen3-VL-30B-A3B-Instruct (Apache-2.0), это следующий шаг.
- На 3090 рабочий стол не должен занимать эту же карту (иначе не хватит ~1 ГБ); иначе — `LOCAL_SLOTS=2` или gpt-oss-20b.

## Источники

1. llm-benchmark.de, RTX 3090, llama.cpp Q4_K_M: gpt-oss-20b 211 tok/s, prefill 4 232 tok/s — https://llm-benchmark.de/benchmark/run-20261024-084507-2c110a ; Qwen3-30B-A3B 189 tok/s, prefill 3 806 — https://llm-benchmark.de/benchmark/run-20261024-084507-3fb87c ; 10 потоков 372 tok/s — https://llm-benchmark.de/benchmark/run-20261024-084507-0f8d84
2. llm-speed, RTX 3090: Qwen3-Coder-30B-A3B Q4_K_M 187–200 tok/s, prefill 3 971; gpt-oss-20b 200–207; dense 27–32B 39 tok/s — https://llm-speed.com/hw/rtx-3090
3. DGX Spark: 128 GB, до 1 PFLOP FP4 — https://www.nvidia.com/en-us/products/workstations/dgx-spark/ ; LPDDR5x до 273 GB/s — обзор LMSYS https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/ ; RTX 3090 936 GB/s — спецификация NVIDIA
4. llama.cpp на DGX Spark (ggerganov), tg32 и pp2048 на глубине 0–32k, batched-bench — https://github.com/ggml-org/llama.cpp/blob/master/benches/dgx-spark/dgx-spark.md
5. NVIDIA Developer Forum, один Spark, Ollama vs vLLM: Qwen3-30B-A3B 4-bit 77,1 (8 потоков 313,4), gpt-oss-120b 60,7 (8 потоков 116–153), падение NVFP4 до ~1,1 tok/s без нативных ядер — https://forums.developer.nvidia.com/t/measured-inference-benchmarks-on-a-single-dgx-spark-same-harness-across-ollama-llama-cpp-and-vllm-notes-data-published/379766/1
6. GPUwerk, форматы на Spark: Qwen3-30B-A3B BF16 31, FP8 50–55, NVFP4 65–66, AWQ 80,9 tok/s, prefill 5 705 на 8k — https://gpuwerk.com/dgx-spark/guides/dgx-spark-quantization-speed/ ; https://gpuwerk.com/docs/benchmarks/
7. Qwen3-30B-A3B-Instruct-2507, карточка модели — https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507
8. GigaChat3-10B-A1.8B (MIT) — https://huggingface.co/ai-sage/GigaChat3-10B-A1.8B ; https://github.com/salute-developers/gigachat3
9. GigaAM-v3 (MIT), WER по доменам — https://huggingface.co/ai-sage/GigaAM-v3 ; https://developers.sber.ru/kak-v-sbere/culture/gigaAM-v3
10. T-one (Apache-2.0) — https://huggingface.co/t-tech/T-one ; https://github.com/voicekit-team/T-one
11. GigaAM-Multilingual-v2 (MIT), WER по 21 языку — https://huggingface.co/ai-sage/GigaAM-Multilingual-v2
12. faster-whisper, бенчмарки на GPU/CPU — https://github.com/SYSTRAN/faster-whisper#benchmark ; whisper-large-v3-turbo (MIT) — https://huggingface.co/openai/whisper-large-v3-turbo
13. Silero: основной репозиторий CC BY-NC(-SA), `v5_cis_base*` — MIT — https://github.com/snakers4/silero-models (раздел «V5 CIS Base Models», LICENSE_CIS)
14. gpt-oss (Apache-2.0) — https://huggingface.co/openai/gpt-oss-120b ; https://huggingface.co/openai/gpt-oss-20b
15. vLLM на DGX Spark (NVIDIA playbook) — https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md
