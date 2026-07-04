#!/usr/bin/env bash
# Восстанавливает data/ и Qdrant volume из backup/, созданного backup_data.sh.
# Запускать из корня склонированного репозитория, backup/ должна лежать рядом.
#
# ВАЖНО: имя тома Qdrant docker-compose генерирует из имени папки проекта —
# если папка называется иначе, чем у того, кто делал бэкап, замени
# QDRANT_VOLUME на актуальное (сначала создастся docker-compose'ом при первом
# `docker compose up`, либо создай вручную: docker volume create <имя>).

set -euo pipefail
cd "$(dirname "$0")/.."

# Git Bash на Windows иначе перепишет пути вида /volume, /backup в аргументах
# `docker run -v` на неверные Windows-пути (реальный баг, встречался в этой сессии).
export MSYS_NO_PATHCONV=1

QDRANT_VOLUME="nornickel-hack_llm-witnesses_qdrant-data"
IN_DIR="backup"

if [ ! -f "$IN_DIR/data.tar.gz" ]; then
  echo "Не найден $IN_DIR/data.tar.gz — положи backup/ рядом с репозиторием" >&2
  exit 1
fi

echo "[restore] data/ ..."
tar xzf "$IN_DIR/data.tar.gz"

echo "[restore] Qdrant volume ($QDRANT_VOLUME)..."
docker volume create "$QDRANT_VOLUME" >/dev/null
docker run --rm \
  -v "$QDRANT_VOLUME":/volume \
  -v "$(pwd)/$IN_DIR":/backup \
  alpine tar xzf /backup/qdrant-data.tar.gz -C /volume

echo "[restore] готово — можно docker compose up (граф и индекс уже на месте, переиндексация не нужна)"
