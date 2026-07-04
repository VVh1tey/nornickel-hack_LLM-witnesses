# Фабрика гипотез

Интеллектуальный инструмент генерации и приоритизации исследовательских гипотез
для обогатительных фабрик: на входе — цель/KPI, ограничения и отчёт по хвостам
(Excel), на выходе — ранжированный список проверяемых гипотез с обоснованием,
ссылками на источники, оценкой новизны/риска/эффекта и дорожной картой проверки.

Состоит из двух независимых сервисов:
- **backend/** — FastAPI + LangGraph-пайплайн (этот README в основном про него)
- **frontend/** — Streamlit-интерфейс, ходит в backend по HTTP

Полная история решений и таск-борд хакатона — в `PLAN.md` (расширять не нужно,
там прошлое; актуальная документация — здесь).

## Как это работает

Есть два разных процесса, которые легко перепутать:

### 1. Разовая офлайн-подготовка: построение графа знаний

Это происходит **один раз** (и заново — только когда добавляются новые
документы), не на каждый запрос пользователя:

```
Учебники (PDF) + регламенты (сканы/PNG) + Excel-примеры + DOCX с реальными
гипотезами экспертов
        │
        ▼
build_corpus.py  — превращает всё это в один corpus.jsonl (текстовые чанки)
        │
        ▼
index_lightrag.py — скармливает corpus.jsonl в LightRAG: для каждого файла
                    LLM извлекает сущности (минералы, оборудование, параметры
                    режима...) и связи между ними → граф знаний (GraphML) +
                    векторный индекс чанков (Qdrant)
```

Без этого шага RAG (поиск по базе знаний) в пайплайне просто ничего не найдёт —
граф изначально пуст. Как запустить и следить за прогрессом — см.
[«Индексация корпуса»](#индексация-корпуса-в-lightrag) ниже.

### 2. Обработка одного запроса пользователя:

Это происходит **на каждый запрос** (`POST /api/sessions`), уже поверх готового
графа знаний:

```
Excel хвостов ──► Tails Analyzer (pandas) ──► LossFinding[] ─┐
                                                              ├─► Generator (LLM) ─► Verification ─► Ranker ─► Roadmap
Цель + ограничения ──► TargetSpec (LLM) ──► Query Builder ───┘         ▲
                                                                        │
                                            Retrieval (LightRAG: граф + Qdrant) ─┘
```

1. **Analyzer** — детерминированный разбор Excel хвостов (без LLM): какие
   элементы/классы крупности/минеральные формы теряются и почему (pandas-правила).
2. **Target Spec** — LLM превращает свободный текст цели/ограничений в
   структурированную задачу (KPI, ограничения, оборудование).
3. **Query Builder** — по находкам анализа строит 5-10 поисковых запросов к базе знаний.
4. **Retrieval** — эти запросы идут в LightRAG (режим `mix`: граф + вектора),
   возвращаются релевантные чанки/сущности/связи.
5. **Generator** — LLM на основе находок + контекста из RAG + реальных примеров
   гипотез с других фабрик (few-shot, сгруппированный по технологическим сферам —
   классификация/измельчение/флотация/автоматизация, чтобы не копировать
   пропорции тем few-shot) генерирует 8-15 новых гипотез.
6. **Verification** — для каждой гипотезы: не нарушает ли ограничения (LLM),
   не совпадает ли по сути с уже опробованным (эмбеддинг-поиск + LLM-подтверждение),
   правдоподобен ли механизм с точки зрения физики/химии (LLM-критик).
7. **Ranker** — LLM-судья оценивает каждую гипотезу по 4 критериям (новизна,
   реализуемость, эффект, риск) 1-5; взвешенная сумма — по весам из UI, без LLM.
8. **Roadmap** — для топ-5 гипотез LLM строит шаги лабораторной/промышленной
   проверки с ресурсами и критериями успеха.

Каждый узел — обычный async-вызов нашего `llm/client.py` (Ollama или Yandex),
не через LangChain — поэтому в Langfuse трейсится и уровень узлов (через
LangGraph `CallbackHandler`), и содержимое каждого отдельного вызова LLM
(вручную, `tracing.py`).

## Быстрый старт (Docker, весь стек)

```bash
cp .env.example .env
# сгенерировать секреты Langfuse (см. таблицу переменных ниже) и вписать в .env

docker compose up -d ollama qdrant postgres langfuse-postgres langfuse-clickhouse langfuse-minio langfuse-redis langfuse-worker langfuse-web
docker compose run --rm ollama-pull            # разово: скачать модель qwen2.5:7b

docker compose up --build api frontend -d
```

- Backend API: http://localhost:8000
- Frontend (Streamlit): http://localhost:8501
- Langfuse (трейс вызовов LLM): http://localhost:3000
- Qdrant dashboard: http://localhost:6333/dashboard

**Важно про `--build`**: не запускай несколько `docker compose build`
параллельно в разных терминалах — на Windows/WSL2 это может уронить Docker
Desktop (VHDX не размонтируется). Один `docker compose up --build <сервисы> -d`
за раз — безопасно, он сам последовательно/параллельно соберёт указанные сервисы.

### Индексация корпуса в LightRAG

```bash
docker compose --profile tools up -d indexer   # фоном
docker compose logs -f indexer                 # следить за прогрессом
```

На маленькой локальной модели (Ollama, CPU/GPU) это может занять от получаса
до нескольких часов на полном корпусе (десятки книг/файлов, у каждого файла —
внутренние чанки, на каждый чанк отдельный вызов LLM на извлечение сущностей).
Резюмируется безопасно: LightRAG дедуплицирует по имени файла и кэширует
LLM-ответы, повторный запуск на уже обработанных файлах бесплатен.

Проверить прогресс, не читая логи целиком:

```bash
docker exec <indexer-контейнер> python -c "
import json
from collections import Counter
d = json.load(open('/app/data/lightrag/kv_store_doc_status.json', encoding='utf-8'))
print(len(d), 'файлов в очереди/обработано')
print(Counter(v.get('status') for v in d.values()))
"
curl http://localhost:8000/api/debug/graph/stats   # растущее число узлов/рёбер
```

### Добавление нового документа в корпус

1. Положить файл в `Задача 1. Фабрика гипотез/Задача 1/...` (туда, куда смотрит `build_corpus.py`)
2. Пересобрать корпус: `cd backend && uv run python scripts/build_corpus.py`
   (перегенерирует `corpus.jsonl`/`hypotheses_db.json`/`equipment.json`)
3. Проиндексировать: `docker compose --profile tools up -d indexer` — обработается
   только новый файл, старые не пересчитаются (дедуп + кэш)

Отдельной кнопки «загрузить документ» в UI сейчас нет — это ручной процесс
через файловую систему + пересборку корпуса, не через фронтенд.

## Разработка (uv, без Docker)

`pyproject.toml`/`uv.lock` у бэка и фронта раздельные (осознанное решение) —
команды `uv` запускаются **из соответствующей директории**:

```bash
cd backend
uv sync
uv run python scripts/build_corpus.py       # собрать corpus.jsonl / hypotheses_db.json / equipment.json
uv run python scripts/index_lightrag.py --pilot   # быстрый пилот (2 файла корпуса)
uv run pytest                               # тесты
uv run uvicorn hypofactory.api.app:app --reload
```

```bash
cd frontend
uv sync
uv run streamlit run app.py                # http://localhost:8501, читает ../configs/api.json
```

Для разработки без Docker нужны локально доступные `ollama serve`
(`ollama pull qwen2.5:7b`), Qdrant (`docker run -p 6333:6333 qdrant/qdrant`) и
Postgres — либо просто `docker compose up -d ollama qdrant postgres` и работать
с `api`/скриптами через `uv run` (сервисы слушают на localhost).

## Фронтенд (Streamlit) — что есть где

Интерфейс на русском, у каждой кнопки/слайдера есть подсказка при наведении.
Три вкладки:

- **🎯 Цель и параметры** — загрузка Excel, необязательное имя сессии
  (человекочитаемое — иначе в списках только UUID; активную сессию можно
  переименовать и позже, поле появляется там же после создания), ввод цели
  (свободный текст, например «Снизить потери элементов 28 и 29 с хвостами») и
  ограничений, настройка весов критериев ранжирования (слайдеры
  Релевантность/Новизна/Реализуемость/Эффект), запуск сессии. Кнопка
  **Пересчитать рейтинг (Rerank)** здесь же — пересчитывает итоговый score по
  новым весам **без повторного вызова LLM** (оценки 1-5 по каждому критерию уже
  посчитаны один раз при генерации, rerank просто пересортировывает список) —
  можно жать сколько угодно раз, мгновенно и бесплатно.
- **💡 Гипотезы** — список сгенерированных гипотез с кнопками **👍 Принять** /
  **👎 Отклонить** (human-in-the-loop фидбэк, пишется в сессию через
  `POST .../feedback`). Под каждой гипотезой — раскрывающийся блок
  **«Комментарий эксперта и перегенерация»**: свободный текст с тем, что не
  так/что поправить, и кнопка **Перегенерировать** — LLM переписывает
  формулировку гипотезы с учётом комментария и заново прогоняет её через
  verify/rank/roadmap (`POST .../regenerate`), id и место в списке сохраняются.
- **📊 Аналитика** — детали активной сессии (имя, ID, статус, цель/ограничения),
  прогресс по узлам пайплайна, сводка (сколько гипотез принято/отклонено/без
  решения, средний score) и таблица со всеми оценками ранжирования
  (новизна/реализуемость/эффект/риск/score/статус) по каждой гипотезе — то, чего
  не видно в списке во вкладке «Гипотезы». Плюс кнопки экспорта.

**Экспорт в CSV / JSON / DOCX** — после нажатия появляется отдельная кнопка
**💾 Скачать** с реальным файлом (сохранённые байты ответа API, а не
предпросмотр на странице).

## Переменные окружения (`.env`)

| Переменная | Назначение |
|---|---|
| `LLM_PROVIDER` | `ollama` (локальный Qwen, по умолчанию) \| `yandex` |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | адрес Ollama и модель (по умолчанию `qwen2.5:7b`) |
| `YC_FOLDER_ID`, `YC_API_KEY` | доступ к Yandex AI Studio (нужны только при `LLM_PROVIDER=yandex` и/или `EMBEDDING_PROVIDER=yandex`) |
| `YC_MODEL` | модель генерации/экстракции YandexGPT (по умолчанию `yandexgpt`) |
| `YC_VISION_MODEL` | модель для описания схем/регламентов (`gemma-3-27b-it` — единственная с поддержкой изображений на момент написания; у Ollama-провайдера vision не реализован) |
| `EMBEDDING_PROVIDER` | `local` (bge-m3, по умолчанию) \| `yandex` (text-embeddings-v2) |
| `EMBEDDING_DIM` | размерность эмбеддингов Yandex (128/256/512/768); для `local` фиксирована на 1024 |
| `QDRANT_URL` | адрес Qdrant (векторный индекс LightRAG) |
| `POSTGRES_DSN` | строка подключения к Postgres (история сессий) |
| `LLM_MAX_CONCURRENCY` | лимит параллельных вызовов LLM и эмбеддингов |
| `HYPOFACTORY_FAKE_LLM=1` | форсировать `FakeLLM`/фейковые эмбеддинги независимо от провайдера (тесты, отладка без сети вообще) |
| `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | трейс вызовов LLM (self-hosted Langfuse, см. ниже) |
| `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY`, `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_PG_PASSWORD`, `LANGFUSE_CLICKHOUSE_PASSWORD`, `LANGFUSE_MINIO_PASSWORD`, `LANGFUSE_REDIS_AUTH` | секреты self-hosted Langfuse-стека (сгенерировать: `openssl rand -hex 16` / `32` / `12`, см. `.env.example`) |
| `LANGFUSE_INIT_USER_EMAIL`, `LANGFUSE_INIT_USER_PASSWORD` | логин/пароль для входа в Langfuse UI при первом старте |

## API

Хранение сессий — Postgres (`api/store.py`, JSONB-блоб на сессию): без него
поднятого сессии не сохранятся (сервис специально не падает обратно на файлы).

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/sessions` | создать сессию: загрузить Excel + цель/ограничения (+опц. `name`), пайплайн стартует в фоне |
| GET | `/api/sessions?limit=&offset=` | список сессий (новые сверху) |
| GET | `/api/sessions/{id}` | статус, прогресс по узлам пайплайна, гипотезы (когда готовы) |
| POST | `/api/sessions/{id}/name` | переименовать сессию: `{"name": "..."}` |
| POST | `/api/sessions/{id}/hypotheses/{hid}/feedback` | HITL: `{"action": "approve"\|"reject"\|"skip", "comment": "..."}` (`comment` необязателен) |
| POST | `/api/sessions/{id}/hypotheses/{hid}/regenerate` | переписать гипотезу с учётом `{"comment": "..."}` (LLM) и заново прогнать verify/rank/roadmap |
| POST | `/api/sessions/{id}/rerank` | пересортировка по новым весам критериев без вызова LLM |
| GET | `/api/sessions/{id}/export?format=csv\|json\|docx` | выгрузка гипотез |
| GET | `/api/sessions/{id}/hypotheses/{hid}/graph` | HTML-граф сущностей/связей вокруг гипотезы |
| GET | `/api/debug/graph` | HTML всего графа знаний целиком (у LightRAG в этой интеграции нет своего дашборда) |
| GET | `/api/debug/graph/stats` | JSON: сколько узлов/рёбер в графе |

Примеры:

```bash
# создать сессию
curl -X POST http://localhost:8000/api/sessions \
  -F "excel_file=@Хвосты КГМК.xlsx" \
  -F "goal=Снизить потери Элемента 28 с хвостами" \
  -F "constraints=Без остановки текущей схемы"
# -> {"session_id": "...", "status": "running"}

# статус/результат
curl http://localhost:8000/api/sessions/<session_id>

# фидбэк эксперта
curl -X POST http://localhost:8000/api/sessions/<session_id>/hypotheses/<hid>/feedback \
  -H "Content-Type: application/json" -d '{"action": "approve"}'

# пересортировка по весам
curl -X POST http://localhost:8000/api/sessions/<session_id>/rerank \
  -H "Content-Type: application/json" \
  -d '{"novelty": 2, "feasibility": 1, "impact": 3, "risk": 1}'

# экспорт
curl -OJ http://localhost:8000/api/sessions/<session_id>/export?format=docx
```

## Оценка качества (deepeval)

```bash
cd backend && uv run python eval/run_eval.py
```

Гоняет полный пайплайн на held-out Примере 4 (ТОФ — единственный, чьи реальные
гипотезы экспертов НЕ входят ни в few-shot генератора, ни в базу «уже
пробовали»), считает:
- **Coverage vs эксперты** — сколько из 8 реальных гипотез экспертов ТОФ
  воспроизвёл пайплайн (LLM-judge на смысловое совпадение)
- **GEval** (настоящий deepeval) — конкретность/проверяемость каждой гипотезы
- существующие ranker-оценки, для полноты картины

Результат — на экран, в `backend/eval/last_report.json` и в наглядный
`backend/eval/last_report.html` (открыть в браузере).

## Дашборды и наблюдаемость

- **Qdrant** (векторный индекс LightRAG): `http://localhost:6333/dashboard`
- **Postgres** (история сессий): любой клиент (psql/DBeaver/PyCharm) по
  `POSTGRES_DSN` (`localhost:5432`, `hypofactory`/`hypofactory`)
- **Langfuse** (трейс каждого вызова LLM — узлы пайплайна + отдельные
  запросы/ответы генератора/верификатора/ранкера/roadmap): `http://localhost:3000`
- **Граф знаний LightRAG** (у самого LightRAG нет встроенного дашборда в этой
  интеграции) — сохранить и открыть в браузере:
  ```bash
  curl http://localhost:8000/api/debug/graph -o graph.html
  start graph.html   # PowerShell/cmd; на Linux/macOS — open/xdg-open graph.html
  ```

## Известные ограничения

- Одна из книг в «Дополнительные материалы» — скан без текстового слоя
  (`geokniga_lodeyshchikovvv...pdf`, 455 стр.); нужен постраничный vision-OCR,
  сейчас не индексируется (дорого по API за отведённое время).
- Held-out: «Гипотезы ТОФ.docx» (Пример 4) используется только для
  `backend/eval/goldsets/tof_expected.json`, не входит ни в few-shot, ни в
  «уже пробовали» — иначе оценка качества на нём была бы нечестной.
- `LLM_PROVIDER=ollama` не поддерживает vision (описание схем/регламентов) —
  для ingestion картинок нужен `LLM_PROVIDER=yandex`.
- LightRAG выводит в логи `WARNING: Rerank is enabled but no rerank model is
  configured` — реранкер не подключён (не критично для качества, просто
  предупреждение); `insert_custom_kg` из сидов equipment.json — тоже не
  реализован (см. PLAN.md §8).
- Если `check_llm.py`/пайплайн с `LLM_PROVIDER=yandex` падает с
  `PERMISSION_DENIED`/`403` при заполненных ключах — у сервисного аккаунта нет
  роли `ai.languageModels.user` (или аналогичной для эмбеддингов) на каталоге
  `YC_FOLDER_ID`, либо AI Studio не включён на этом каталоге. Правится в
  консоли Yandex Cloud, не в коде.
- `ainsert()` в LightRAG дедуплицирует по basename файла — `index_lightrag.py`
  поэтому склеивает чанки одного файла обратно в один документ перед вставкой
  (иначе индексируется только первый чанк каждого файла).
- Интерфейс фронтенда сейчас на английском (лейблы кнопок/полей) — перевод на
  русский не сделан из-за нехватки времени.
- Кнопки/флоу «добавить документ в корпус» в UI нет — см.
  [«Добавление нового документа в корпус»](#добавление-нового-документа-в-корпус) выше (ручной процесс).
