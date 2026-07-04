"""retrieve(query, k) -> RetrievalResult — единая точка входа в LightRAG для пайплайна.

mode="mix" (граф + вектор) с одним фолбэком на mode="naive", если граф пуст/недоступен
(например, индексация ещё не запускалась) — см. PLAN.md §1 (Sufficiency check упрощён
до одной итерации).
"""

from __future__ import annotations

from lightrag import QueryParam

from hypofactory.rag.lightrag_setup import get_lightrag
from hypofactory.schemas import DocumentChunk, RetrievalResult


def _relation_label(rel: dict) -> str:
    src = rel.get("src_id", "?")
    tgt = rel.get("tgt_id", "?")
    keywords = rel.get("keywords") or "связан с"
    return f"{src} -[{keywords}]-> {tgt}"


async def retrieve(query: str, k: int = 10, mode: str = "mix") -> RetrievalResult:
    rag = await get_lightrag()
    param = QueryParam(mode=mode, top_k=k, chunk_top_k=k, include_references=True)

    result = await rag.aquery_llm(query, param)

    if result.get("status") != "success":
        if mode != "naive":
            return await retrieve(query, k=k, mode="naive")
        return RetrievalResult(chunks=[], entities=[], relations=[])

    data = result.get("data", {})

    chunks = [
        DocumentChunk(
            chunk_id=ch.get("chunk_id") or ch.get("reference_id") or "",
            source_file=ch.get("file_path") or "unknown_source",
            doc_type="textbook_pdf",
            page_or_sheet=1,
            content=ch.get("content", ""),
        )
        for ch in data.get("chunks", [])
        if ch.get("content")
    ]
    entities = [e.get("entity_name", "") for e in data.get("entities", []) if e.get("entity_name")]
    relations = [_relation_label(r) for r in data.get("relationships", [])]

    return RetrievalResult(chunks=chunks, entities=entities, relations=relations)
