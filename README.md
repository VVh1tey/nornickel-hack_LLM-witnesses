# Фабрика гипотез

Интеллектуальный инструмент генерации и приоритизации исследовательских гипотез
для обогатительных фабрик: на входе — цель/KPI, ограничения и отчёт по хвостам
(Excel), на выходе — ранжированный список проверяемых гипотез с обоснованием,
ссылками на источники, оценкой новизны/риска/эффекта и дорожной картой проверки.

Полная архитектура и таск-борд — в `PLAN.md`. Ниже — как поднять бэкенд и что
у него за API (фронтенд — отдельный сервис, подключается по HTTP).

## Архитектура (коротко)

```
Excel хвостов ──► Tails Analyzer (pandas) ──► LossFinding[] ─┐
                                                              ├─► Generator (LLM) ─► Verification ─► Ranker ─► Roadmap ─► API
Цель + ограничения ──► TargetSpec (LLM) ──► Query Builder ───┘         ▲
                                                                        │
Учебники/схемы/регламенты/DOCX-гипотезы ──► LightRAG (граф + Qdrant) ──┘
```

Инфраструктура: **Ollama** (локальный Qwen, LLM по умолчанию) или **YandexGPT**
(`yandex-ai-studio-sdk`) — переключатель `LLM_PROVIDER`; **bge-m3** локально или
**Yandex text-embeddings-v2** — переключатель `EMBEDDING_PROVIDER`; **Qdrant** —
векторный индекс LightRAG; **Postgres** — история сессий. Всё поднимается
`docker-compose.yml`. Без вообще какого-либо провайдера — прозрачный `FakeLLM`
(`HYPOFACTORY_FAKE_LLM=1`), пайплайн и тесты работают полностью без сети.

## Быстрый старт (Docker)

```bash
cp .env.example .env
docker compose up -d ollama qdrant postgres    # локальный стек (без ключей Yandex)
docker compose run --rm ollama-pull            # разово: скачать модель qwen3.5:4b
docker compose build
docker compose up api                          # http://localhost:8000
```

Индексация корпуса в LightRAG (фоном — вызовы LLM на извлечение сущностей,
на маленькой локальной модели это часы на полном корпусе):

```bash
docker compose run --rm indexer
```

Проверить, что активный LLM-провайдер вообще отвечает, ДО индексации:

```bash
cd backend && uv run python scripts/check_llm.py
```

## Разработка (uv, без Docker)

`pyproject.toml`/`uv.lock` бэка и фронта разделены — команды `uv` запускаются
**из `backend/`** (или `frontend/` — для фронта отдельно):

```bash
cd backend
uv sync
uv run python scripts/build_corpus.py       # собрать corpus.jsonl / hypotheses_db.json / equipment.json
uv run python scripts/index_lightrag.py --pilot   # быстрый пилот (2 файла корпуса)
uv run pytest                               # тесты
uv run uvicorn hypofactory.api.app:app --reload
```

Для разработки без Docker нужны локально доступные `ollama serve`
(`ollama pull qwen3.5:4b`), Qdrant (`docker run -p 6333:6333 qdrant/qdrant`) и
Postgres — либо просто `docker compose up -d ollama qdrant postgres` и работать
с `api`/скриптами через `uv run` (сервисы слушают на localhost).

## Переменные окружения (`.env`)

| Переменная | Назначение |
|---|---|
| `LLM_PROVIDER` | `ollama` (локальный Qwen, по умолчанию) \| `yandex` |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | адрес Ollama и модель (по умолчанию `qwen3.5:4b`) |
| `YC_FOLDER_ID`, `YC_API_KEY` | доступ к Yandex AI Studio (нужны только при `LLM_PROVIDER=yandex` и/или `EMBEDDING_PROVIDER=yandex`) |
| `YC_MODEL` | модель генерации/экстракции YandexGPT (по умолчанию `yandexgpt`) |
| `YC_VISION_MODEL` | модель для описания схем/регламентов (`gemma-3-27b-it` — единственная с поддержкой изображений на момент написания; у Ollama-провайдера vision не реализован) |
| `EMBEDDING_PROVIDER` | `local` (bge-m3, по умолчанию) \| `yandex` (text-embeddings-v2) |
| `EMBEDDING_DIM` | размерность эмбеддингов Yandex (128/256/512/768); для `local` фиксирована на 1024 |
| `QDRANT_URL` | адрес Qdrant (векторный индекс LightRAG) |
| `POSTGRES_DSN` | строка подключения к Postgres (история сессий) |
| `LLM_MAX_CONCURRENCY` | лимит параллельных вызовов LLM и эмбеддингов |
| `HYPOFACTORY_FAKE_LLM=1` | форсировать `FakeLLM`/фейковые эмбеддинги независимо от провайдера (тесты, отладка без сети вообще) |

## API

Хранение сессий — Postgres (`api/store.py`, JSONB-блоб на сессию): без него
поднятого сессии не сохранятся (сервис специально не падает обратно на файлы).

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/sessions` | создать сессию: загрузить Excel + цель/ограничения, пайплайн стартует в фоне |
| GET | `/api/sessions?limit=&offset=` | список сессий (новые сверху) |
| GET | `/api/sessions/{id}` | статус, прогресс по узлам пайплайна, гипотезы (когда готовы) |
| POST | `/api/sessions/{id}/hypotheses/{hid}/feedback` | HITL: `{"action": "approve"\|"reject"\|"skip"}` |
| POST | `/api/sessions/{id}/rerank` | пересортировка по новым весам критериев без вызова LLM |
| GET | `/api/sessions/{id}/export?format=csv\|json\|docx` | выгрузка гипотез |
| GET | `/api/sessions/{id}/hypotheses/{hid}/graph` | HTML-граф сущностей/связей вокруг гипотезы |
| GET | `/api/debug/graph` | HTML всего графа знаний целиком (у LightRAG в этой интеграции нет своего дашборда) |
| GET | `/api/debug/graph/stats` | JSON: сколько узлов/рёбер в графе |

Дашборды: Qdrant — `http://localhost:6333/dashboard`; Postgres — любой клиент
(psql/DBeaver/PyCharm) по `POSTGRES_DSN` (`localhost:5432`, `hypofactory`/`hypofactory`).
Граф LightRAG — сохранить и открыть в браузере:
```bash
curl http://localhost:8000/api/debug/graph -o graph.html
start graph.html   # PowerShell/cmd; на Linux/macOS — open/xdg-open graph.html
```

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

## Известные ограничения

- Одна из 5 книг в «Дополнительные материалы» — скан без текстового слоя
  (`geokniga_lodeyshchikovvv...pdf`, 455 стр.); нужен постраничный vision-OCR,
  сейчас не индексируется (дорого по API за отведённое время).
- Held-out: «Гипотезы ТОФ.docx» (Пример 4) используется только для
  `backend/eval/goldsets/tof_expected.json`, не входит ни в few-shot, ни в
  «уже пробовали» — иначе оценка качества на нём была бы нечестной.
- `LLM_PROVIDER=ollama` не поддерживает vision (описание схем/регламентов) —
  для ingestion картинок нужен `LLM_PROVIDER=yandex`.
- Реранкер (bge-reranker) и LightRAG `insert_custom_kg` из сидов equipment.json —
  в cut-list, не реализованы (см. PLAN.md §8).
- Если `check_llm.py`/пайплайн с `LLM_PROVIDER=yandex` падает с
  `PERMISSION_DENIED`/`403` при заполненных ключах — у сервисного аккаунта нет
  роли `ai.languageModels.user` (или аналогичной для эмбеддингов) на каталоге
  `YC_FOLDER_ID`, либо AI Studio не включён на этом каталоге. Правится в
  консоли Yandex Cloud, не в коде.
- `ainsert()` в LightRAG дедуплицирует по basename файла — `index_lightrag.py`
  поэтому склеивает чанки одного файла обратно в один документ перед вставкой
  (иначе индексируется только первый чанк каждого файла).
