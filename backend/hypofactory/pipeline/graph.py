"""Сборка полного пайплайна B->I в LangGraph. run_pipeline() — контракт для API/UI:
    async for event in run_pipeline(...): PipelineStatus (прогресс) | list[Hypothesis] (финал)
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional, TypedDict, Union

from langgraph.graph import END, START, StateGraph

from hypofactory import config, tracing
from hypofactory.analysis.tails_analyzer import analyze as analyze_tails
from hypofactory.ingestion.excel_tails import parse_loss_table
from hypofactory.pipeline.generator import generate_hypotheses
from hypofactory.pipeline.query_builder import build_queries
from hypofactory.pipeline.ranker import rank
from hypofactory.pipeline.roadmap import build_roadmap
from hypofactory.pipeline.target_spec import parse_target
from hypofactory.pipeline.verification import build_known_hypotheses_index, verify
from hypofactory.rag.retrieve import retrieve as rag_retrieve
from hypofactory.schemas import (
    Hypothesis,
    LossFinding,
    PipelineStatus,
    RankingWeights,
    RetrievalResult,
    TargetSpec,
)

_ROADMAP_TOP_N = 5
_QUERIES_PER_RUN = 5


class PipelineState(TypedDict, total=False):
    excel_path: str
    goal: str
    constraints: str
    weights: RankingWeights
    findings: list[LossFinding]
    spec: TargetSpec
    queries: list[str]
    retrieval: RetrievalResult
    hypotheses: list[Hypothesis]


def _load_hypotheses_db() -> dict[str, list[str]]:
    if config.HYPOTHESES_DB_PATH.exists():
        return json.loads(config.HYPOTHESES_DB_PATH.read_text(encoding="utf-8"))
    return {}


async def _node_analyzer(state: PipelineState) -> dict[str, Any]:
    parsed = parse_loss_table(state["excel_path"])
    return {"findings": analyze_tails(parsed)}


async def _node_target_spec(state: PipelineState) -> dict[str, Any]:
    spec = await parse_target(state["goal"], state.get("constraints", ""))
    return {"spec": spec}


async def _node_query_builder(state: PipelineState) -> dict[str, Any]:
    return {"queries": build_queries(state["findings"], state["spec"])}


async def _node_retrieval(state: PipelineState) -> dict[str, Any]:
    merged_chunks: list = []
    merged_entities: list[str] = []
    merged_relations: list[str] = []
    seen_ids: set[str] = set()

    for query in state["queries"][:_QUERIES_PER_RUN]:
        result = await rag_retrieve(query, k=5)
        for chunk in result.chunks:
            if chunk.chunk_id not in seen_ids:
                merged_chunks.append(chunk)
                seen_ids.add(chunk.chunk_id)
        for entity in result.entities:
            if entity not in merged_entities:
                merged_entities.append(entity)
        for relation in result.relations:
            if relation not in merged_relations:
                merged_relations.append(relation)

    return {"retrieval": RetrievalResult(chunks=merged_chunks, entities=merged_entities, relations=merged_relations)}


async def _node_generator(state: PipelineState) -> dict[str, Any]:
    hypotheses_db = _load_hypotheses_db()
    hyps = await generate_hypotheses(state["spec"], state["findings"], state["retrieval"], hypotheses_db)
    return {"hypotheses": hyps}


async def _node_verification(state: PipelineState) -> dict[str, Any]:
    hypotheses_db = _load_hypotheses_db()
    known_index = await build_known_hypotheses_index(hypotheses_db)  # один раз на весь прогон
    context_texts = [c.content for c in state["retrieval"].chunks]
    verified = [await verify(h, state["spec"], known_index, context_texts) for h in state["hypotheses"]]
    return {"hypotheses": verified}


async def _node_ranker(state: PipelineState) -> dict[str, Any]:
    weights = state.get("weights") or RankingWeights()
    ranked = await rank(state["hypotheses"], weights)
    return {"hypotheses": ranked}


async def _node_roadmap(state: PipelineState) -> dict[str, Any]:
    hyps = state["hypotheses"]
    for hyp in hyps[:_ROADMAP_TOP_N]:
        hyp.roadmap = await build_roadmap(hyp)
    return {"hypotheses": hyps}


def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("analyzer", _node_analyzer)
    graph.add_node("target_spec", _node_target_spec)
    graph.add_node("query_builder", _node_query_builder)
    graph.add_node("retrieval", _node_retrieval)
    graph.add_node("generator", _node_generator)
    graph.add_node("verification", _node_verification)
    graph.add_node("ranker", _node_ranker)
    graph.add_node("roadmap", _node_roadmap)

    graph.add_edge(START, "analyzer")
    graph.add_edge("analyzer", "target_spec")
    graph.add_edge("target_spec", "query_builder")
    graph.add_edge("query_builder", "retrieval")
    graph.add_edge("retrieval", "generator")
    graph.add_edge("generator", "verification")
    graph.add_edge("verification", "ranker")
    graph.add_edge("ranker", "roadmap")
    graph.add_edge("roadmap", END)
    return graph.compile()


_NODE_MESSAGES = {
    "analyzer": "Анализ потерь металлов в хвостах",
    "target_spec": "Разбор цели и ограничений",
    "query_builder": "Построение поисковых запросов",
    "retrieval": "Поиск по базе знаний (LightRAG)",
    "generator": "Генерация гипотез",
    "verification": "Проверка гипотез (ограничения, дубликаты, физика)",
    "ranker": "Ранжирование гипотез",
    "roadmap": "Построение дорожной карты проверки",
}


async def run_pipeline(
    excel_path: str,
    goal: str,
    constraints: str = "",
    weights: Optional[RankingWeights] = None,
    session_id: Optional[str] = None,
) -> AsyncIterator[Union[PipelineStatus, list[Hypothesis]]]:
    app = build_graph()
    initial_state: PipelineState = {
        "excel_path": excel_path,
        "goal": goal,
        "constraints": constraints,
        "weights": weights or RankingWeights(),
    }

    # трейс узлов пайплайна в Langfuse (см. tracing.py) — no-op, если
    # LANGFUSE_PUBLIC_KEY/SECRET_KEY не заданы
    handler = tracing.get_langchain_handler()
    run_config: dict[str, Any] = {}
    if handler is not None:
        run_config["callbacks"] = [handler]
        run_config["run_name"] = "hypofactory-pipeline"
        if session_id:
            run_config["metadata"] = {"langfuse_session_id": session_id}

    final_hypotheses: list[Hypothesis] = []
    async for event in app.astream(initial_state, stream_mode="updates", config=run_config or None):
        for node_name, node_output in event.items():
            yield PipelineStatus(node=node_name, message=_NODE_MESSAGES.get(node_name, node_name), done=True)
            if node_output and "hypotheses" in node_output:
                final_hypotheses = node_output["hypotheses"]

    yield final_hypotheses
