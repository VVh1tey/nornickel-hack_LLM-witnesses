"""Конфигурация: пути, модели, ключи. Всё из .env (см. .env.example в корне)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
LIGHTRAG_DIR = DATA_DIR / "lightrag"

CORPUS_PATH = PROCESSED_DIR / "corpus.jsonl"
HYPOTHESES_DB_PATH = PROCESSED_DIR / "hypotheses_db.json"
EQUIPMENT_PATH = PROCESSED_DIR / "equipment.json"

# LLM — два провайдера на выбор (см. llm/client.py):
#   "ollama" — маленький Qwen локально в контейнере (по умолчанию, для отладки
#              без ключей/квот/прав Yandex), OpenAI-совместимый /api/chat.
#   "yandex" — YandexGPT через yandex-ai-studio-sdk (нужны YC_FOLDER_ID/YC_API_KEY).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# Yandex AI Studio SDK (пакет yandex-ai-studio-sdk)
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID", "")
YC_API_KEY = os.getenv("YC_API_KEY", "")
# модель для генерации/экстракции: pro, не lite — качество экстракции определяет граф
YC_MODEL = os.getenv("YC_MODEL", "yandexgpt")
# на момент написания только эта модель в AI Studio понимает картинки (sdk.chat.completions)
YC_VISION_MODEL = os.getenv("YC_VISION_MODEL", "gemma-3-27b-it")

# Ollama (локальный маленький Qwen, сервис в docker-compose.yml)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
# реранкер LightRAG (llm/rerank.py) — та же переменная качает docker-compose
# сервис ollama-pull, чтобы модель в .env и то, что реально скачано, не разъезжались
RERANK_MODEL = os.getenv("RERANK_MODEL", "dengcao/Qwen3-Reranker-0.6B:Q8_0")
# потолок длины ответа Ollama в токенах — защита от срыва модели в
# галлюцинацию вместо короткого JSON (см. llm/client.py._chat_raw)
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "4096"))

LLM_CACHE_DIR = DATA_DIR / "llm_cache"

# Эмбеддинги — см. llm/embeddings.py. Два провайдера на выбор:
#   "local"  — bge-m3 локально (по умолчанию: не зависит от прав/квот Yandex,
#              надёжный вариант, пока не разобрались с PERMISSION_DENIED)
#   "yandex" — text-embeddings-v2 (doc/query асимметрично). Короткие имена
#              sdk.models.text_embeddings("doc"/"query") — это v1 (жёстко 256 dim);
#              v2 даёт 128/256/512/768 — берём максимум.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")  # только для EMBEDDING_PROVIDER=local

if EMBEDDING_PROVIDER == "local":
    EMBEDDING_DIM = 1024  # фиксированная размерность bge-m3, override .env тут бессмысленен
else:
    EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))

# Векторный индекс LightRAG: Qdrant (сервис в docker-compose.yml). LightRAG сам
# читает QDRANT_URL/QDRANT_API_KEY из os.environ (см. lightrag/kg/qdrant_impl.py) —
# load_dotenv() выше уже положил их туда, здесь только для наглядности/дефолта.
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("QDRANT_URL", QDRANT_URL)

# История сессий — Postgres (см. api/store.py). Без него сессии не сохранятся:
# сервис специально не хранит их в JSON-фолбэке, поднимай docker-compose postgres.
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://hypofactory:hypofactory@localhost:5432/hypofactory")

# Лимиты конкурентности вызовов LLM/эмбеддингов
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "4"))

CHUNK_TOKEN_SIZE = 1100
CHUNK_OVERLAP = 150

# Предметная область (см. domain_profile.py) — параметризует entity_types для
# LightRAG и формулировку "ты эксперт по..." в промптах generator/verification/
# ranker/roadmap. Новый домен подключается профилем там же, без правок кода.
DOMAIN_PROFILE = os.getenv("DOMAIN_PROFILE", "obogashchenie")

# Авторизация API (см. api/app.py: middleware проверяет заголовок X-API-Key на
# всех /api/* маршрутах). Пусто по умолчанию — авторизация ВЫКЛЮЧЕНА (локальная
# разработка/тесты без лишней церемонии). Если разворачиваешь куда-то, кроме
# localhost — обязательно задай непустой API_KEY в .env.
API_KEY = os.getenv("API_KEY", "")

# Langfuse (self-hosted, docker-compose.yml) — трейс вызовов LLM, см. tracing.py.
# SDK (langfuse.get_client()) сам читает эти переменные из os.environ — здесь
# только для наглядности/дефолта, как с QDRANT_URL выше.
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
os.environ.setdefault("LANGFUSE_HOST", LANGFUSE_HOST)
