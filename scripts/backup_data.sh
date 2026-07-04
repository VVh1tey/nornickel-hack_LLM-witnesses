#!/usr/bin/env bash
# Упаковывает всё, что нужно для переноса проекта на другую машину БЕЗ
# повторной индексации LightRAG: граф знаний + корпус + LLM-кэш (обычные
# файлы в data/) и векторный индекс Qdrant (живёт внутри Docker-тома, файлом
# не лежит — архивируем через служебный alpine-контейнер).
#
# НЕ архивируем:
#   - модели Ollama (несколько ГБ) — на новом месте просто:
#     docker compose run --rm ollama-pull
#   - историю сессий Postgres — без неё приложение работает нормально,
#     просто список сессий будет пустым; если нужна и она — раскомментируй блок ниже.
#
# Имя томаqdrant подставляется docker-compose из имени папки проекта — если
# у получателя папка называется иначе, замени QDRANT_VOLUME на реальное имя
# (посмотреть: docker volume ls | grep qdrant-data).

set -euo pipefail
cd "$(dirname "$0")/.."

# Git Bash на Windows иначе перепишет пути вида /volume, /backup в аргументах
# `docker run -v` на неверные Windows-пути (реальный баг, встречался в этой сессии).
export MSYS_NO_PATHCONV=1

QDRANT_VOLUME="nornickel-hack_llm-witnesses_qdrant-data"
OUT_DIR="backup"

mkdir -p "$OUT_DIR"

echo "[backup] data/ (граф LightRAG, корпус, LLM-кэш)..."
tar czf "$OUT_DIR/data.tar.gz" data/

echo "[backup] Qdrant volume ($QDRANT_VOLUME)..."
docker run --rm \
  -v "$QDRANT_VOLUME":/volume \
  -v "$(pwd)/$OUT_DIR":/backup \
  alpine tar czf /backup/qdrant-data.tar.gz -C /volume .

# Опционально — история сессий Postgres:
# POSTGRES_VOLUME="nornickel-hack_llm-witnesses_postgres-data"
# docker run --rm \
#   -v "$POSTGRES_VOLUME":/volume \
#   -v "$(pwd)/$OUT_DIR":/backup \
#   alpine tar czf /backup/postgres-data.tar.gz -C /volume .

echo "[backup] готово: $OUT_DIR/data.tar.gz, $OUT_DIR/qdrant-data.tar.gz"
echo "[backup] дальше: заархивируй сам репозиторий (git clone/zip) + папку $OUT_DIR — и передай оба вместе"
