#!/usr/bin/env python3
"""
Восстанавливает data/ и Qdrant volume из backup/, созданного backup.py.
Запускать из корня склонированного репозитория, backup/ должна лежать рядом.

ВАЖНО: имя тома Qdrant docker-compose генерирует из имени папки проекта —
если папка называется иначе, чем у того, кто делал бэкап, замени
qdrant_volume на актуальное (сначала создастся docker-compose'ом при первом
`docker compose up`, либо создай вручную: docker volume create <имя>).
"""

import os
import sys
import tarfile
import subprocess
from pathlib import Path

def main():
    # Переходим в родительскую директорию (аналог cd "$(dirname "$0")/..")
    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir.parent)

    qdrant_volume = "nornickel-hack_llm-witnesses_qdrant-data"
    in_dir = Path("backup")
    data_tar_path = in_dir / "data.tar.gz"

    # Проверка наличия бэкапа
    if not data_tar_path.is_file():
        print(f"❌ Не найден {data_tar_path} — положи папку {in_dir.name}/ рядом с репозиторием", file=sys.stderr)
        sys.exit(1)

    print("[restore] data/ ...")
    try:
        # Распаковка папки data/ в корень проекта
        with tarfile.open(data_tar_path, "r:gz") as tar:
            # В Python 3.12+ можно добавить filter='data' для безопасности, 
            # но для локальных бэкапов стандартный extractall работает отлично.
            tar.extractall(path=".")
    except Exception as e:
        print(f"❌ Ошибка при распаковке {data_tar_path}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[restore] Qdrant volume ({qdrant_volume})...")
    
    # Создаем том (аналог docker volume create ... >/dev/null)
    # Используем check=False, так как если том уже есть, это не критичная ошибка
    subprocess.run(["docker", "volume", "create", qdrant_volume], stdout=subprocess.DEVNULL, check=False)

    # Получаем абсолютный путь для корректного маппинга томов Docker (решает проблему с путями на Windows)
    backup_path_absolute = str(in_dir.resolve())

    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{qdrant_volume}:/volume",
        "-v", f"{backup_path_absolute}:/backup",
        "alpine", "tar", "xzf", "/backup/qdrant-data.tar.gz", "-C", "/volume"
    ]

    try:
        subprocess.run(docker_cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка при восстановлении в Docker: {e}", file=sys.stderr)
        sys.exit(1)

    print("[restore] готово — можно запускать `docker compose up` (граф и индекс уже на месте, переиндексация не нужна)")

if __name__ == "__main__":
    main()