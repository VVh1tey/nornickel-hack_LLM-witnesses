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

# Yandex AI Studio SDK (пакет yandex-ai-studio-sdk)
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID", "")
YC_API_KEY = os.getenv("YC_API_KEY", "")
# модель для генерации/экстракции: pro, не lite — качество экстракции определяет граф
YC_MODEL = os.getenv("YC_MODEL", "yandexgpt")

# Эмбеддинги — локальные (bge-m3), НЕ Яндекс (256 dim + жёсткие квоты)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = 1024

# Лимиты под квоты Яндекса
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "4"))

CHUNK_TOKEN_SIZE = 1100
CHUNK_OVERLAP = 150
