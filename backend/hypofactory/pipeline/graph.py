"""Сборка полного пайплайна B->I в LangGraph. run_pipeline() — контракт для API/UI:
    async for event in run_pipeline(...): PipelineStatus (прогресс) | list[Hypothesis] (финал)
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional, TypedDict, Union

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from hypofactory import config, tracing
from hypofactory.analysis.registry import get_analyzer
from hypofactory.domain_profile import get_profile
from hypofactory.pipeline.feedback_learning import load_approved_pool
from hypofactory.pipeline.generator import _load_hypotheses_db, generate_hypotheses
from hypofactory.pipeline.query_builder import build_queries
from hypofactory.pipeline.ranker import compute_weighted_score, score_hypothesis
from hypofactory.pipeline.rejected_filter import filter_against_rejected
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
_TARGET_N_HYPOTHESES = 10
# генерируем с запасом: часть отсеется фильтром по сходству с отклонёнными
# (rejected_filter) — без запаса пользователь получал бы меньше гипотез, чем
# ожидает, каждый раз как что-то попадает под фильтр
_GENERATION_BUFFER = 3


def _progress(node: str, message: str) -> None:
    """Прогресс ИЗНУТРИ узла (не только по его завершении) — иначе на долгих
    циклах (verification/ranker/roadmap считают каждую гипотезу отдельным
    вызовом LLM) пользователь видит "идёт проверка" и не понимает, сколько
    осталось. Молча ничего не делает вне контекста графа (напр. если функцию
    вызвали напрямую в тестах, а не через app.astream)."""
    try:
        get_stream_writer()({"node": node, "message": message})
    except RuntimeError:
        pass


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


async def _node_analyzer(state: PipelineState) -> dict[str, Any]:
    # Домен (config.DOMAIN_PROFILE) выбирает анализатор через реестр — новый
    # домен подключается профилем + новым анализатором, без правок графа.
    profile = get_profile(config.DOMAIN_PROFILE)
    analyzer_fn = get_analyzer(profile.analyzer)
    return {"findings": analyzer_fn(state["excel_path"])}


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

    queries = state["queries"][:_QUERIES_PER_RUN]
    for i, query in enumerate(queries, start=1):
        _progress("retrieval", f"Запрос {i}/{len(queries)}: «{query[:60]}»")
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
    hyps = await generate_hypotheses(
        state["spec"], state["findings"], state["retrieval"], n=_TARGET_N_HYPOTHESES + _GENERATION_BUFFER
    )
    return {"hypotheses": hyps}


async def _node_reject_filter(state: PipelineState) -> dict[str, Any]:
    _progress("reject_filter", f"Сверка {len(state['hypotheses'])} гипотез с ранее отклонёнными")
    filtered = await filter_against_rejected(state["hypotheses"])
    return {"hypotheses": filtered[:_TARGET_N_HYPOTHESES]}


async def _node_verification(state: PipelineState) -> dict[str, Any]:
    hypotheses_db = _load_hypotheses_db()
    approved_pool = load_approved_pool()
    if approved_pool:
        # одобренные ранее гипотезы тоже считаются "уже пробовали" — не имеет
        # смысла заново предлагать эксперту то же самое как новую идею
        hypotheses_db = {**hypotheses_db, "Одобрено экспертом ранее": [p["statement"] for p in approved_pool]}
    known_index = await build_known_hypotheses_index(hypotheses_db)  # один раз на весь прогон
    context_texts = [c.content for c in state["retrieval"].chunks]

    hyps = state["hypotheses"]
    verified = []
    for i, h in enumerate(hyps, start=1):
        _progress("verification", f"Гипотеза {i}/{len(hyps)}: «{h.statement[:50]}»")
        verified.append(await verify(h, state["spec"], known_index, context_texts))
    return {"hypotheses": verified}


async def _node_ranker(state: PipelineState) -> dict[str, Any]:
    weights = state.get("weights") or RankingWeights()
    hyps = state["hypotheses"]
    for i, hyp in enumerate(hyps, start=1):
        _progress("ranker", f"Оценка {i}/{len(hyps)}: «{hyp.statement[:50]}»")
        await score_hypothesis(hyp)
        hyp.score = compute_weighted_score(hyp, weights)
    hyps.sort(key=lambda h: h.score or 0, reverse=True)
    return {"hypotheses": hyps}


async def _node_roadmap(state: PipelineState) -> dict[str, Any]:
    hyps = state["hypotheses"]
    top = hyps[:_ROADMAP_TOP_N]
    for i, hyp in enumerate(top, start=1):
        _progress("roadmap", f"Дорожная карта {i}/{len(top)}: «{hyp.statement[:50]}»")
        hyp.roadmap = await build_roadmap(hyp)
    return {"hypotheses": hyps}


def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("analyzer", _node_analyzer)
    graph.add_node("target_spec", _node_target_spec)
    graph.add_node("query_builder", _node_query_builder)
    graph.add_node("retrieval", _node_retrieval)
    graph.add_node("generator", _node_generator)
    graph.add_node("reject_filter", _node_reject_filter)
    graph.add_node("verification", _node_verification)
    graph.add_node("ranker", _node_ranker)
    graph.add_node("roadmap", _node_roadmap)

    graph.add_edge(START, "analyzer")
    graph.add_edge("analyzer", "target_spec")
    graph.add_edge("target_spec", "query_builder")
    graph.add_edge("query_builder", "retrieval")
    graph.add_edge("retrieval", "generator")
    graph.add_edge("generator", "reject_filter")
    graph.add_edge("reject_filter", "verification")
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
    "reject_filter": "Отсев гипотез, похожих на ранее отклонённые экспертом",
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
    # "updates" — событие по завершении узла (как раньше); "custom" — то, что
    # узлы шлют через _progress() изнутри долгих циклов (см. выше) — без
    # этого пользователь видит только "идёт проверка" без понимания, сколько
    # гипотез уже обработано и сколько осталось.
    async for mode, event in app.astream(initial_state, stream_mode=["updates", "custom"], config=run_config or None):
        if mode == "custom":
            yield PipelineStatus(node=event["node"], message=event["message"], done=False)
            continue
        for node_name, node_output in event.items():
            yield PipelineStatus(node=node_name, message=_NODE_MESSAGES.get(node_name, node_name), done=True)
            if node_output and "hypotheses" in node_output:
                final_hypotheses = node_output["hypotheses"]

    yield final_hypotheses
