"""Эмбеддинги: два провайдера на выбор через config.EMBEDDING_PROVIDER (см. .env):

    "local"  — bge-m3 локально (по умолчанию). Не зависит от прав/квот Yandex —
               надёжный вариант для сквозной проверки пайплайна, пока с ключом
               разбираются права (PERMISSION_DENIED на стороне Yandex Cloud).
    "yandex" — text-embeddings-v2, dim=768, асимметрично doc/query. Важно:
               короткие имена sdk.models.text_embeddings("doc"/"query") — это
               алиасы ТОЛЬКО на v1 (жёстко 256 dim, см. _well_known_names в SDK);
               для v2 нужно полное имя модели + .configure(dimensions=768).
               API — по одному тексту за вызов (батча нет), поэтому список
               эмбеддим параллельно с ограничением конкурентности.

Используется как EmbeddingFunc.func в LightRAG (обязан быть async — см.
lightrag/utils.py: `result = await self.func(*args, **kwargs)`). LightRAG сам
передаёт context="document" при индексации и context="query" при поиске
(см. lightrag/kg/nano_vector_db_impl.py) — этим выбираем doc- или query-модель
для провайдера "yandex" (для "local" контекст не важен, модель симметричная).

Без реальных ключей ИЛИ при HYPOFACTORY_FAKE_LLM=1 — детерминированная фейковая
заглушка (как FakeLLM в llm/client.py), чтобы пайплайн и тесты работали без сети.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Optional

import numpy as np

from hypofactory import config
from hypofactory.llm.client import has_real_credentials, is_fake_forced

_DOC_MODEL_NAME = "text-embeddings-v2-doc"
_QUERY_MODEL_NAME = "text-embeddings-v2-query"

_local_model: Optional[Any] = None
_sdk: Optional[Any] = None
_semaphore: Optional[asyncio.Semaphore] = None


def _get_local_model() -> Any:
    global _local_model
    if _local_model is None:
        import torch
        from sentence_transformers import SentenceTransformer

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _local_model = SentenceTransformer(config.EMBEDDING_MODEL, device=device)
    return _local_model


def warmup() -> None:
    """Форсирует загрузку (и, если нужно, скачивание ~2GB весов) локальной модели
    ДО того, как её впервые вызовет LightRAG: у него жёсткий ~60с таймаут на
    embedding-воркер, а первое скачивание bge-m3 почти гарантированно дольше —
    без прогрева insert/query падают с IndexFlushError/WorkerTimeoutError.
    Не нужен при EMBEDDING_PROVIDER=yandex (там нет локальной загрузки)."""
    if config.EMBEDDING_PROVIDER == "local" and not is_fake_forced():
        _get_local_model()


def _get_sdk() -> Any:
    global _sdk
    if _sdk is None:
        from yandex_ai_studio_sdk import AsyncAIStudio

        _sdk = AsyncAIStudio(folder_id=config.YC_FOLDER_ID, auth=config.YC_API_KEY)
    return _sdk


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(config.LLM_MAX_CONCURRENCY)
    return _semaphore


def _fake_vector(text: str) -> np.ndarray:
    """Детерминированный псевдослучайный вектор из хэша текста: одинаковый текст
    -> одинаковый вектор, разный текст -> разный — этого достаточно, чтобы
    cosine_similarity вело себя разумно в тестах без сети."""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    vec = rng.normal(size=config.EMBEDDING_DIM).astype(np.float32)
    return vec / np.linalg.norm(vec)


async def _embed_batch_local(texts: list[str]) -> np.ndarray:
    model = _get_local_model()
    vecs = await asyncio.to_thread(model.encode, texts, normalize_embeddings=True)
    return np.asarray(vecs, dtype=np.float32)


async def _embed_one_yandex(text: str, is_query: bool) -> np.ndarray:
    model_name = _QUERY_MODEL_NAME if is_query else _DOC_MODEL_NAME
    model = _get_sdk().models.text_embeddings(model_name).configure(dimensions=config.EMBEDDING_DIM)
    async with _get_semaphore():
        result = await model.run(text)
    vec = np.asarray(result.embedding, dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


async def _embed_batch_yandex(texts: list[str], is_query: bool) -> np.ndarray:
    vectors = await asyncio.gather(*(_embed_one_yandex(t, is_query) for t in texts))
    return np.stack(vectors).astype(np.float32)


async def aembed(texts: list[str], context: str = "document", **_kwargs: Any) -> np.ndarray:
    """context: "document" (индексация) | "query" (поиск) — так их называет
    LightRAG; **_kwargs проглатывает служебные параметры вроде _priority."""
    if not texts:
        return np.zeros((0, config.EMBEDDING_DIM), dtype=np.float32)

    if is_fake_forced():
        return np.stack([_fake_vector(t) for t in texts]).astype(np.float32)

    if config.EMBEDDING_PROVIDER == "local":
        return await _embed_batch_local(texts)

    if not has_real_credentials():
        return np.stack([_fake_vector(t) for t in texts]).astype(np.float32)

    return await _embed_batch_yandex(texts, is_query=(context == "query"))


def embed(texts: list[str], context: str = "document") -> np.ndarray:
    """Синхронная обёртка для кода вне event loop (напр. разовые скрипты).
    НЕЛЬЗЯ вызывать изнутри уже запущенного event loop — используй aembed()."""
    return asyncio.run(aembed(texts, context=context))


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a: (n, d) уже нормированные эмбеддинги, b: (m, d) — вернёт (n, m) косинусов."""
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.float32)
    return a @ b.T


async def cached_embed_texts(texts: list[str], cache_path: Path) -> np.ndarray:
    """Эмбеддинги с кэшем на диске (.npz: texts + embeddings) — пересчитывает
    только те тексты, которых не было в кэше с прошлого раза. Нужен для
    РАСТУЩИХ пулов (одобренные/отклонённые гипотезы — см. pipeline/
    feedback_learning.py), иначе на каждый запрос эмбеддили бы весь пул заново.
    Возвращает эмбеддинги строго в порядке texts (не в порядке кэша)."""
    if not texts:
        return np.zeros((0, config.EMBEDDING_DIM), dtype=np.float32)

    cached_texts: list[str] = []
    cached_embeddings = np.zeros((0, config.EMBEDDING_DIM), dtype=np.float32)
    if cache_path.exists():
        data = np.load(cache_path, allow_pickle=True)
        cached_texts = list(data["texts"])
        cached_embeddings = data["embeddings"]

    cached_index = {t: i for i, t in enumerate(cached_texts)}
    new_texts = [t for t in dict.fromkeys(texts) if t not in cached_index]  # dedup + порядок

    if new_texts:
        new_embeddings = await aembed(new_texts, context="document")
        all_texts = cached_texts + new_texts
        all_embeddings = (
            np.vstack([cached_embeddings, new_embeddings]) if cached_embeddings.size else new_embeddings
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_path, texts=np.array(all_texts, dtype=object), embeddings=all_embeddings)
        cached_index = {t: i for i, t in enumerate(all_texts)}
        cached_embeddings = all_embeddings

    return cached_embeddings[[cached_index[t] for t in texts]]
