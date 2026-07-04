"""Индексация data/processed/corpus.jsonl в LightRAG.

ВАЖНО: LightRAG дедуплицирует "документы" по basename file_path (см.
lightrag/pipeline.py: "3a. Filename-based dedup: same basename always treated
as duplicate") — один ainsert-вызов должен соответствовать ОДНОМУ уникальному
файлу, а не отдельному чанку. Наш corpus.jsonl уже нарезан на чанки постранично
(pdf_books.py и т.п.) — если передать их все с одинаковым file_paths, LightRAG
пропустит все чанки файла, кроме первого (проверено на пилоте: из 50 чанков
одной книги выжил 1). Поэтому здесь чанки одного файла склеиваются обратно в
один текст и передаются ОДНИМ вызовом ainsert — LightRAG сам нарежет их заново
своим chunking_func (chunk_token_size из config.py); page-level метаданные при
этом теряются, но file_path-цитирование сохраняется (retrieve.py его и использует).

Тяжёлая часть: 5 книг — это вызовы LLM на извлечение сущностей по каждому
внутреннему чанку. Запускать пилотом сначала (--pilot — первые 2 файла),
затем полный корпус ФОНОМ (см. docker-compose.yml, сервис indexer, профиль tools):
    docker compose run --rm indexer
    uv run python backend/scripts/index_lightrag.py            # весь корпус
    uv run python backend/scripts/index_lightrag.py --pilot     # быстрый пилот
LightRAG кэширует LLM-ответы сам — повторный запуск на тех же файлах бесплатен.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hypofactory import config
from hypofactory.rag.lightrag_setup import get_lightrag
from hypofactory.schemas import DocumentChunk


def _load_corpus() -> list[DocumentChunk]:
    chunks = []
    with open(config.CORPUS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(DocumentChunk.model_validate_json(line))
    return chunks


def _group_by_file(chunks: list[DocumentChunk]) -> list[tuple[str, str]]:
    """Возвращает [(file_path, полный_текст)] — чанки одного файла в порядке
    появления в корпусе склеены обратно (см. docstring модуля)."""
    by_file: dict[str, list[str]] = defaultdict(list)
    for c in chunks:
        by_file[c.source_file].append(c.content)
    return [(source_file, "\n\n".join(texts)) for source_file, texts in by_file.items()]


async def main(pilot: bool = False, pilot_files: int = 2) -> None:
    chunks = _load_corpus()
    files = _group_by_file(chunks)
    if pilot:
        files = files[:pilot_files]

    print(f"[index_lightrag] файлов к индексации: {len(files)} (из {len(chunks)} чанков корпуса)")

    rag = await get_lightrag()
    for i, (source_file, full_text) in enumerate(files, start=1):
        print(f"[index_lightrag] [{i}/{len(files)}] {source_file} ({len(full_text)} символов)")
        await rag.ainsert(full_text, file_paths=source_file)

    print("[index_lightrag] готово")


if __name__ == "__main__":
    asyncio.run(main(pilot="--pilot" in sys.argv))
