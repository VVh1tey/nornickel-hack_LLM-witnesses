"""Реранкер поверх LightRAG retrieval: Qwen3-Reranker-0.6B через Ollama
(модель задаётся RERANK_MODEL в .env; скачивается автоматически командой
`docker compose run --rm ollama-pull`, см. docker-compose.yml).

Отдельная маленькая модель, не участвует в генерации/верификации/ранкере
гипотез — только пересортировывает retrieval-результаты LightRAG перед их
использованием в generator (см. lightrag_setup.py: rerank_model_func).

LightRAG сам вызывает эту функцию с сигнатурой (query, documents, top_n) и
ждёт [{"index": i, "relevance_score": float}, ...] — см.
lightrag.utils.apply_rerank_if_enabled. Готовых биндингов под Ollama у LightRAG
нет (только cohere/jina/aliyun), поэтому пишем свой: модель просит оценить
релевантность документа запросу и вернуть JSON-скор.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

import httpx

from hypofactory import config

logger = logging.getLogger(__name__)

RERANK_MODEL = config.RERANK_MODEL

# Модель маленькая и быстрая, но обрабатывает документы по одному — держим
# конкурентность отдельно от основного LLM_MAX_CONCURRENCY, чтобы не душить
# основную модель во время индексации/генерации.
_semaphore = asyncio.Semaphore(4)

_SCORE_RE = re.compile(r'"score"\s*:\s*([0-9.]+)')


def _prompt(query: str, document: str) -> str:
    return (
        "Оцени релевантность документа запросу пользователя по шкале от 0 "
        "(совсем не по теме) до 1 (прямо отвечает на запрос).\n"
        f"Запрос: {query}\n"
        f"Документ: {document[:2000]}\n"
        'Ответь строго JSON без пояснений: {"score": число от 0 до 1}.'
    )


async def _score_one(client: httpx.AsyncClient, query: str, document: str) -> float:
    async with _semaphore:
        try:
            response = await client.post(
                f"{config.OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": RERANK_MODEL,
                    "messages": [{"role": "user", "content": _prompt(query, document)}],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.0},
                },
            )
            response.raise_for_status()
            text = response.json()["message"]["content"]
        except Exception:
            logger.warning("Реранкер: не удалось получить оценку, документ идёт с нейтральным скором", exc_info=True)
            return 0.5

    try:
        return max(0.0, min(1.0, float(json.loads(text)["score"])))
    except Exception:
        match = _SCORE_RE.search(text)
        if match:
            try:
                return max(0.0, min(1.0, float(match.group(1))))
            except ValueError:
                pass
        return 0.5


async def rerank(
    query: str,
    documents: list[str],
    top_n: Optional[int] = None,
    **_kwargs: Any,
) -> list[dict]:
    """Сигнатура строго под LightRAG rerank_model_func (см. lightrag.utils.
    apply_rerank_if_enabled) — не менять имена параметров/форму ответа."""
    if not documents:
        return []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
        scores = await asyncio.gather(*(_score_one(client, query, doc) for doc in documents))

    results = [{"index": i, "relevance_score": s} for i, s in enumerate(scores)]
    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    if top_n is not None:
        results = results[:top_n]
    return results
