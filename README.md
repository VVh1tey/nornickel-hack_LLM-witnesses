# Фабрика гипотез

Инструмент генерации и приоритизации исследовательских гипотез для
обогатительных фабрик: на входе — цель/KPI, ограничения и отчёт по хвостам
(Excel), на выходе — ранжированный список проверяемых гипотез с обоснованием,
ссылками на источники, оценкой новизны/риска/эффекта и дорожной картой проверки.

Два независимых сервиса:
- **backend/** — FastAPI + LangGraph-пайплайн (этот README в основном про него)
- **frontend/** — Streamlit-интерфейс, ходит в backend по HTTP

Архитектура и история решений — в `PLAN.md`, если интересны детали хакатона.
Актуальная инструкция — здесь.

## Как это работает

Два разных процесса — легко перепутать:

### 1. Построение графа знаний (разовая офлайн-подготовка)

Запускается один раз и заново — только когда добавляются новые документы:

```
Учебники (PDF) + регламенты (сканы/PNG) + Excel-примеры + DOCX с реальными
гипотезами экспертов
        │
        ▼
build_corpus.py  — собирает всё в один corpus.jsonl (текстовые чанки)
        │
        ▼
index_lightrag.py — индексирует corpus.jsonl в LightRAG: LLM извлекает
                    сущности (минералы, оборудование, параметры режима...) и
                    связи между ними → граф знаний (GraphML) + векторный
                    индекс чанков (Qdrant)
```

Без этого шага поиск по базе знаний ничего не найдёт — граф изначально пуст.
Как запустить и следить за прогрессом — см. [«Индексация корпуса»](#индексация-корпуса-в-lightrag).

### 2. Обработка запроса пользователя

Происходит на каждый вызов `POST /api/sessions`, уже поверх готового графа:

```
Excel хвостов ──► Tails Analyzer (pandas) ──► LossFinding[] ─┐
                                                              ├─► Generator (LLM) ─► Verification ─► Ranker ─► Roadmap
Цель + ограничения ──► TargetSpec (LLM) ──► Query Builder ───┘         ▲
                                                                        │
                                            Retrieval (LightRAG: граф + Qdrant) ─┘
```

1. **Analyzer** — детерминированный разбор Excel хвостов (без LLM): какие
   элементы/классы крупности/минеральные формы теряются и почему.
2. **Target Spec** — LLM превращает свободный текст цели/ограничений в
   структурированную задачу (KPI, ограничения, оборудование).
3. **Query Builder** — по находкам анализа строит поисковые запросы к базе знаний.
4. **Retrieval** — запросы идут в LightRAG (граф + вектора), возвращаются
   релевантные чанки, сущности и связи.
5. **Generator** — LLM на основе находок, контекста из RAG и реальных
   гипотез с других фабрик генерирует новые гипотезы.
6. **Verification** — для каждой гипотезы: не нарушает ли ограничения, не
   совпадает ли по сути с уже опробованным, правдоподобен ли механизм с
   точки зрения физики/химии.
7. **Ranker** — LLM-судья оценивает гипотезу по 4 критериям (новизна,
   реализуемость, эффект, риск); итоговый score считается по весам из UI.
8. **Roadmap** — для лучших гипотез строится план проверки: шаги, ресурсы,
   сроки, критерии успеха.

Все вызовы LLM трейсятся в Langfuse — и на уровне узлов пайплайна, и по
содержимому каждого запроса/ответа.

## Быстрый старт (Docker, весь стек)

```bash
cp .env.example .env
# сгенерировать секреты Langfuse (см. таблицу переменных ниже) и вписать в .env

docker compose up -d ollama qdrant postgres langfuse-postgres langfuse-clickhouse langfuse-minio langfuse-redis langfuse-worker langfuse-web
docker compose run --rm ollama-pull            # разово: скачать LLM (qwen2.5:7b) и реранкер

docker compose up --build api frontend -d
```

- Backend API: http://localhost:8000
- Frontend (Streamlit): http://localhost:8501
- Langfuse (трейс вызовов LLM): http://localhost:3000
- Qdrant dashboard: http://localhost:6333/dashboard

Не запускай несколько `docker compose build` параллельно в разных
терминалах — на Windows/WSL2 это может уронить Docker Desktop. Один
`docker compose up --build <сервисы> -d` за раз.

### Индексация корпуса в LightRAG

```bash
docker compose --profile tools up -d indexer   # фоном
docker compose logs -f indexer                 # следить за прогрессом
```

На маленькой локальной модели это может занять от получаса до нескольких
часов на полном корпусе — на каждый чанк отдельный вызов LLM на извлечение
сущностей. Повторный запуск безопасен: LightRAG дедуплицирует по имени файла
и кэширует LLM-ответы, уже обработанные файлы не пересчитываются.

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
3. Проиндексировать: `docker compose --profile tools up -d indexer` — обработается
   только новый файл

Кнопки «загрузить документ» в UI нет — это ручной процесс через файловую
систему, не через фронтенд.

## Разработка (uv, без Docker)

У бэка и фронта отдельные `pyproject.toml`/`uv.lock` — команды `uv`
запускаются из соответствующей директории:

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

Для разработки без Docker нужны локально доступные `ollama serve` с моделями
(`ollama pull qwen2.5:7b` и `ollama pull dengcao/Qwen3-Reranker-0.6B:Q8_0`,
или `docker compose run --rm ollama-pull`), Qdrant (`docker run -p 6333:6333
qdrant/qdrant`) и Postgres — либо просто `docker compose up -d ollama qdrant
postgres`, а `api`/скрипты гонять через `uv run` (сервисы слушают на localhost).

## Фронтенд (Streamlit)

Интерфейс на русском, у каждой кнопки/слайдера есть подсказка при наведении.
Три вкладки:

- **🎯 Цель и параметры** — загрузка Excel, имя сессии (необязательное —
  иначе в списках только UUID), цель и ограничения свободным текстом
  (например «Снизить потери элементов 28 и 29 с хвостами»), веса критериев
  ранжирования, запуск сессии. Кнопка **Пересчитать рейтинг** здесь же —
  пересортировывает гипотезы по новым весам без обращения к LLM, можно жать
  сколько угодно раз.
- **💡 Гипотезы** — список гипотез с кнопками **Принять** / **Отклонить**.
  Под каждой гипотезой:
  - **Комментарий эксперта и перегенерация** — пишешь, что не так, LLM
    переписывает гипотезу с учётом комментария и заново прогоняет проверку
    и оценку; id гипотезы не меняется.
  - **Дорожная карта проверки** — шаги в виде timeline (plotly) с оценкой
    длительности каждого шага; ниже — редактируемые поля (шаг, ресурсы,
    критерий успеха, срок) с кнопкой «Сохранить», правки не требуют LLM.
    Строится только для лучших по рейтингу гипотез.
- **📊 Аналитика** — детали сессии, прогресс по узлам пайплайна, сводка по
  фидбэку (принято/отклонено/без решения, средний score) и таблица оценок
  по всем критериям для каждой гипотезы.

Экспорт (CSV / JSON / DOCX / PDF) — одна кнопка на формат: жмёшь «Экспорт»,
она превращается в «Скачать» с готовым файлом.

## Переменные окружения (`.env`)

| Переменная | Назначение |
|---|---|
| `LLM_PROVIDER` | `ollama` (локальный Qwen, по умолчанию) \| `yandex` |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | адрес Ollama и модель (по умолчанию `qwen2.5:7b`) |
| `YC_FOLDER_ID`, `YC_API_KEY` | доступ к Yandex AI Studio (только при `LLM_PROVIDER=yandex` и/или `EMBEDDING_PROVIDER=yandex`) |
| `YC_MODEL` | модель генерации/экстракции YandexGPT (по умолчанию `yandexgpt`) |
| `YC_VISION_MODEL` | модель для описания схем/регламентов (`gemma-3-27b-it`; у Ollama-провайдера vision не реализован) |
| `EMBEDDING_PROVIDER` | `local` (bge-m3, по умолчанию) \| `yandex` (text-embeddings-v2) |
| `EMBEDDING_DIM` | размерность эмбеддингов Yandex (128/256/512/768); для `local` фиксирована на 1024 |
| `QDRANT_URL` | адрес Qdrant |
| `POSTGRES_DSN` | строка подключения к Postgres (история сессий) |
| `LLM_MAX_CONCURRENCY` | лимит параллельных вызовов LLM и эмбеддингов |
| `DOMAIN_PROFILE` | предметная область (по умолчанию `obogashchenie`) — см. [«Адаптация под другие домены»](#адаптация-под-другие-домены) |
| `PDF_FONT_PATH` | путь к Unicode TTF-шрифту для PDF, если не найден автоматически |
| `API_KEY` | авторизация API по заголовку `X-API-Key` (пусто = выключена — только для локальной разработки); сгенерировать `openssl rand -hex 24` |
| `HYPOFACTORY_FAKE_LLM=1` | форсировать `FakeLLM`/фейковые эмбеддинги (тесты, отладка без сети) |
| `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | трейс вызовов LLM (self-hosted Langfuse) |
| `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY`, `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_PG_PASSWORD`, `LANGFUSE_CLICKHOUSE_PASSWORD`, `LANGFUSE_MINIO_PASSWORD`, `LANGFUSE_REDIS_AUTH` | секреты self-hosted Langfuse-стека (сгенерировать `openssl rand -hex 16` / `32` / `12`, см. `.env.example`) |
| `LANGFUSE_INIT_USER_EMAIL`, `LANGFUSE_INIT_USER_PASSWORD` | логин/пароль для входа в Langfuse UI при первом старте |

## API

Хранение сессий — Postgres (`api/store.py`, JSONB-блоб на сессию); без него
сессии не сохранятся.

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/sessions` | создать сессию: Excel + цель/ограничения (+опц. `name`), пайплайн стартует в фоне |
| GET | `/api/sessions?limit=&offset=` | список сессий (новые сверху) |
| GET | `/api/sessions/{id}` | статус, прогресс по узлам, гипотезы (когда готовы) |
| POST | `/api/sessions/{id}/name` | переименовать сессию: `{"name": "..."}` |
| POST | `/api/sessions/{id}/hypotheses/{hid}/feedback` | `{"action": "approve"\|"reject"\|"skip", "comment": "..."}` |
| POST | `/api/sessions/{id}/hypotheses/{hid}/regenerate` | переписать гипотезу с учётом `{"comment": "..."}` и заново прогнать verify/rank/roadmap |
| POST | `/api/sessions/{id}/hypotheses/{hid}/roadmap` | ручная правка дорожной карты — без LLM |
| POST | `/api/sessions/{id}/rerank` | пересортировка по новым весам без LLM |
| GET | `/api/sessions/{id}/export?format=csv\|json\|docx\|pdf` | выгрузка гипотез |
| GET | `/api/sessions/{id}/hypotheses/{hid}/graph` | HTML-граф сущностей/связей вокруг гипотезы |
| GET | `/api/debug/graph` | HTML всего графа знаний целиком |
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

Если задан `API_KEY`, каждый запрос должен нести заголовок `-H "X-API-Key: <ключ>"`.

## Оценка качества (deepeval)

```bash
cd backend && uv run python eval/run_eval.py
```

Гоняет полный пайплайн на held-out Примере 4 (ТОФ — единственный, чьи
реальные гипотезы экспертов не входят ни в few-shot, ни в базу «уже
пробовали»), считает:
- **Coverage vs эксперты** — сколько из 8 реальных гипотез экспертов ТОФ
  воспроизвёл пайплайн
- **GEval** — конкретность и проверяемость каждой гипотезы
- ranker-оценки — для полноты картины

Результат — на экран, в `backend/eval/last_report.json` и в наглядный
`backend/eval/last_report.html`.

## Дашборды

- **Qdrant**: `http://localhost:6333/dashboard`
- **Postgres**: любой клиент (psql/DBeaver/PyCharm) по `POSTGRES_DSN`
  (`localhost:5432`, `hypofactory`/`hypofactory`)
- **Langfuse**: `http://localhost:3000` — трейс каждого вызова LLM
- **Граф знаний LightRAG** (своего дашборда нет) — сохранить и открыть в браузере:
  ```bash
  curl http://localhost:8000/api/debug/graph -o graph.html
  start graph.html   # PowerShell/cmd; на Linux/macOS — open/xdg-open graph.html
  ```

## Адаптация под другие домены

`DOMAIN_PROFILE` (`backend/hypofactory/domain_profile.py`) параметризует
LLM-часть пайплайна: роль эксперта во всех системных промптах, типы сущностей
для LightRAG-графа, ключевые слова для группировки few-shot по направлениям.

Новый домен — новая запись в `DOMAIN_PROFILES` + новый анализатор в
`backend/hypofactory/analysis/registry.py` (интерфейс `analyze(file_path) ->
list[LossFinding]`), без правок графа пайплайна.

Анализатор входных данных (`analysis/tails_analyzer.py`) — детерминированные
pandas-правила под конкретную Excel-схему («класс крупности × минеральная
форма × элемент»). Это не параметризуется конфигом: для другого домена нужен
свой анализатор с другой входной схемой данных — отсюда и registry вместо
единого профиля. Vision-OCR промпты (`ingestion/vision_ocr.py`) тоже
предметны (конкретное оборудование в теле промпта) и для нового домена
потребуют отдельной версии.

## Готовый бэкап (быстрый старт без переиндексации)

Уже собранный граф знаний + векторный индекс лежат тут:
https://disk.yandex.ru/d/xn47XDhpcuyeFw

1. Скачать оттуда папку `backup` и положить рядом с репозиторием (на одном
   уровне с `docker-compose.yml`).
2. Запустить Docker Desktop (просто чтобы демон был доступен — весь стек
   поднимать ещё не нужно).
3. Восстановить данные из бэкапа:
   ```bash
   ./scripts/restore_data.sh
   ```
4. Только теперь поднимать стек:
   ```bash
   docker compose up -d ollama qdrant postgres langfuse-postgres langfuse-clickhouse langfuse-minio langfuse-redis langfuse-worker langfuse-web
   docker compose run --rm ollama-pull
   docker compose up --build api frontend -d
   ```

Порядок важен: если сначала поднять Qdrant, а потом восстанавливать том —
Qdrant уже откроет пустое хранилище и не подхватит подложенные файлы без
перезапуска. Восстанавливать нужно ДО `docker compose up`.

## Перенос на другую машину (сделать свой бэкап)

Граф знаний LightRAG лежит обычными файлами в `data/`, а векторный индекс
Qdrant — внутри Docker-тома, файлом не вытащить напрямую. Чтобы передать
проект без повторной индексации (часы работы):

```bash
./scripts/backup_data.sh     # соберёт backup/data.tar.gz + backup/qdrant-data.tar.gz
```

Передать получателю репозиторий (git clone/zip) и папку `backup/` рядом с
ним, дальше на его машине:

```bash
./scripts/restore_data.sh
docker compose up -d
docker compose run --rm ollama-pull   # модели Ollama не архивируются, качаются заново
```

История сессий Postgres в бэкап не входит (без неё список сессий просто
начнётся с нуля, на работу пайплайна не влияет). Имя Qdrant-тома
docker-compose генерирует из имени папки проекта — если у получателя папка
называется иначе, поправь `QDRANT_VOLUME` в обоих скриптах.

## Известные ограничения

- Одна из книг в «Дополнительные материалы» — скан без текстового слоя
  (`geokniga_lodeyshchikovvv...pdf`, 455 стр.); нужен постраничный vision-OCR,
  сейчас не индексируется.
- Held-out: «Гипотезы ТОФ.docx» (Пример 4) используется только для
  `backend/eval/goldsets/tof_expected.json`, не входит ни в few-shot, ни в
  «уже пробовали» — иначе оценка качества на нём была бы нечестной.
- `LLM_PROVIDER=ollama` не поддерживает vision — для ingestion картинок
  нужен `LLM_PROVIDER=yandex`.
- Если пайплайн с `LLM_PROVIDER=yandex` падает с `PERMISSION_DENIED`/`403`
  при заполненных ключах — у сервисного аккаунта нет роли
  `ai.languageModels.user` на каталоге `YC_FOLDER_ID`, либо AI Studio не
  включён на этом каталоге. Правится в консоли Yandex Cloud, не в коде.
- API-авторизация — единый ключ на все запросы, не полноценный RBAC с
  разными правами для разных пользователей. Шифрование данных (в Postgres,
  в транзите) не реализовано.
- `insert_custom_kg` из сидов equipment.json в LightRAG не реализован.
