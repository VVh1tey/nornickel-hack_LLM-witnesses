"""Инициализация LightRAG: граф знаний + векторный индекс поверх corpus.jsonl.

API проверен на установленном lightrag-hku==1.5.4 (см. PLAN.md §3):
    LightRAG(working_dir, llm_model_func, embedding_func, ...)
    await rag.initialize_storages()
    await initialize_pipeline_status()   # из lightrag.kg.shared_storage
Доменные типы сущностей и русский язык — через addon_params, это самый
дешёвый рычаг качества извлечения графа для металлургии/обогащения.

Векторный индекс — Qdrant (vector_storage=QdrantVectorDBStorage), а не файловый
NanoVectorDBStorage по умолчанию: LightRAG сам читает адрес из os.environ
["QDRANT_URL"] (см. lightrag/kg/qdrant_impl.py и lightrag/kg/__init__.py:
STORAGES/env-requirements) — config.py уже кладёт его в os.environ. KV/граф/
doc-status остаются файловыми (JSON/GraphML) в LIGHTRAG_DIR — их Qdrant не
заменяет, запрос был именно про векторное хранилище.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from lightrag import LightRAG
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.utils import EmbeddingFunc

from hypofactory import config
from hypofactory.domain_profile import get_profile
from hypofactory.llm import embeddings
from hypofactory.llm.client import get_client
from hypofactory.llm.embeddings import aembed
from hypofactory.llm.rerank import rerank as rerank_model_func

_rag_instance: Optional[LightRAG] = None


async def get_lightrag() -> LightRAG:
    """Синглтон: инициализируется один раз за процесс (и для индексации, и для retrieve)."""
    global _rag_instance
    if _rag_instance is not None:
        return _rag_instance

    client = get_client()

    async def llm_model_func(
        prompt: str,
        system_prompt: Optional[str] = None,
        history_messages: Optional[list[dict]] = None,
        **kwargs,
    ) -> str:
        return await client.acomplete(
            prompt, system_prompt=system_prompt, history_messages=history_messages or [], **kwargs
        )

    config.LIGHTRAG_DIR.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(embeddings.warmup)

    # Маленькая CPU-модель (Ollama, ~10-15 ток/с) на длинных чанках легко
    # превышает дефолтный таймаут LightRAG (240с/480с воркер) — щедрее таймаут
    # и без "gleaning" (второй LLM-проход "что я упустил?" на каждый чанк
    # удваивает время индексации, для отладки на слабом железе не оправдан).
    is_slow_local_llm = config.LLM_PROVIDER == "ollama"

    rag = LightRAG(
        working_dir=str(config.LIGHTRAG_DIR),
        vector_storage="QdrantVectorDBStorage",
        llm_model_func=llm_model_func,
        embedding_func=EmbeddingFunc(
            embedding_dim=config.EMBEDDING_DIM,
            func=aembed,
            # Qdrant предупреждает без этого: имя нужно для изоляции коллекций,
            # если когда-нибудь сменится модель/провайдер эмбеддингов.
            model_name=f"{config.EMBEDDING_PROVIDER}:{config.EMBEDDING_MODEL if config.EMBEDDING_PROVIDER == 'local' else 'yandex-v2'}",
        ),
        chunk_token_size=config.CHUNK_TOKEN_SIZE,
        chunk_overlap_token_size=config.CHUNK_OVERLAP,
        entity_extract_max_gleaning=0 if is_slow_local_llm else 1,
        default_llm_timeout=900 if is_slow_local_llm else 240,
        llm_model_max_async=config.LLM_MAX_CONCURRENCY,
        # Qwen3-Reranker-0.6B через Ollama (llm/rerank.py) — маленькая
        # отдельная модель, без неё LightRAG молча пропускал rerank с
        # WARNING в логах (enable_rerank=True в QueryParam по умолчанию).
        rerank_model_func=rerank_model_func,
        min_rerank_score=0.3,
        # Дефолт LightRAG (30с) даёт воркеру всего 60с (timeout+30, см.
        # utils.priority_limit_async_func_call) — наш rerank() дергает Ollama
        # по одному документу на каждый из нескольких найденных чанков, на
        # медленной локальной модели это легко дольше 60с суммарно.
        default_rerank_timeout=180 if is_slow_local_llm else 30,
        addon_params={
            "language": "Русский",
            "entity_types": get_profile(config.DOMAIN_PROFILE).entity_types,
        },
    )
    await rag.initialize_storages()
    await initialize_pipeline_status()

    _rag_instance = rag
    return rag
