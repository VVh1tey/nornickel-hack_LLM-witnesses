#!/usr/bin/env python3
"""
Упаковывает всё, что нужно для переноса проекта на другую машину БЕЗ
повторной индексации LightRAG: граф знаний + корпус + LLM-кэш (обычные
файлы в data/) и векторный индекс Qdrant (живёт внутри Docker-тома, файлом
не лежит — архивируем через служебный alpine-контейнер).

НЕ архивируем:
  - модели Ollama (несколько ГБ) — на новом месте просто:
    docker compose run --rm ollama-pull
  - историю сессий Postgres — без неё приложение работает нормально,
    просто список сессий будет пустым; если нужна и она — раскомментируй блок ниже.
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
    out_dir = Path("backup")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[backup] data/ (граф LightRAG, корпус, LLM-кэш)...")
    data_tar_path = out_dir / "data.tar.gz"
    
    # Архивация папки data/ средствами самого Python (работает везде одинаково)
    with tarfile.open(data_tar_path, "w:gz") as tar:
        if Path("data").exists():
            tar.add("data", arcname="data")
        else:
            print("⚠️ Папка data/ не найдена, пропускаем...")

    print(f"[backup] Qdrant volume ({qdrant_volume})...")
    # Получаем абсолютный путь, который Docker поймет и на Mac (/Users/...), и на Win (C:\...)
    backup_path_absolute = str(out_dir.resolve())

    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{qdrant_volume}:/volume",
        "-v", f"{backup_path_absolute}:/backup",
        "alpine", "tar", "czf", "/backup/qdrant-data.tar.gz", "-C", "/volume", "."
    ]

    try:
        subprocess.run(docker_cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка при выполнении Docker: {e}", file=sys.stderr)
        sys.exit(1)

    # Опционально — история сессий Postgres:
    # postgres_volume = "nornickel-hack_llm-witnesses_postgres-data"
    # docker_pg_cmd = [
    #     "docker", "run", "--rm",
    #     "-v", f"{postgres_volume}:/volume",
    #     "-v", f"{backup_path_absolute}:/backup",
    #     "alpine", "tar", "czf", "/backup/postgres-data.tar.gz", "-C", "/volume", "."
    # ]
    # subprocess.run(docker_pg_cmd, check=True)

    print(f"[backup] готово: {data_tar_path}, {out_dir / 'qdrant-data.tar.gz'}")
    print(f"[backup] дальше: заархивируй сам репозиторий (git clone/zip) + папку {out_dir.name} — и передай оба вместе")

if __name__ == "__main__":
    main()