"""FastAPI по контракту, согласованному с фронтом (см. PLAN.md, план слияния):

    POST /api/sessions                                      -> {session_id, status}
    GET  /api/sessions?limit=&offset=                        -> список сессий desc(created_at)
    GET  /api/sessions/{id}                                  -> статус + прогресс + гипотезы
    POST /api/sessions/{id}/name                             -> переименовать сессию (человекочитаемое имя)
    POST /api/sessions/{id}/hypotheses/{hid}/feedback         -> HITL approve/reject/skip (+ опц. comment)
    POST /api/sessions/{id}/hypotheses/{hid}/regenerate       -> переписать гипотезу с учётом comment (LLM) + заново verify/rank/roadmap
    POST /api/sessions/{id}/rerank                            -> пересортировка по новым весам (без LLM)
    GET  /api/sessions/{id}/export?format=csv|json|docx      -> файл
    GET  /api/sessions/{id}/hypotheses/{hid}/graph            -> HTML подграфа

Хранение — Postgres (api/store.py, JSONB на сессию). Пайплайн стартует в BackgroundTasks.
"""

from __future__ import annotations

import shutil
import uuid
from typing import Literal, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

import json

from hypofactory import config
from hypofactory.api.store import SessionState, list_sessions, load_session, save_session
from hypofactory.export.csv_json import export_csv, export_json
from hypofactory.export.docx_report import export_docx
from hypofactory.pipeline.generator import revise_hypothesis
from hypofactory.pipeline.graph import run_pipeline
from hypofactory.pipeline.ranker import compute_weighted_score, rerank as rerank_hypotheses, score_hypothesis
from hypofactory.pipeline.roadmap import build_roadmap
from hypofactory.pipeline.target_spec import parse_target
from hypofactory.pipeline.verification import build_known_hypotheses_index, verify
from hypofactory.rag.graph_viz import graph_stats, render_full_graph_html, render_graph_html
from hypofactory.rag.retrieve import retrieve
from hypofactory.schemas import HypothesisStatus, RankingWeights

app = FastAPI(title="Фабрика гипотез API")

UPLOADS_DIR = config.DATA_DIR / "sessions" / "uploads"

_ACTION_TO_STATUS = {
    "approve": HypothesisStatus.APPROVED,
    "reject": HypothesisStatus.REJECTED,
    "skip": HypothesisStatus.SKIPPED,
}


async def _run_pipeline_background(
    session_id: str, excel_path: str, goal: str, constraints: str, weights: RankingWeights
) -> None:
    session = await load_session(session_id)
    if session is None:
        return
    try:
        async for event in run_pipeline(excel_path, goal, constraints, weights, session_id=session_id):
            if isinstance(event, list):
                session.hypotheses = event
                session.status = "done"
            else:
                session.progress.append(event)
            await save_session(session)
    except Exception as e:  # noqa: BLE001 — сессия должна отразить ошибку, а не уронить BackgroundTask молча
        session.status = "error"
        session.error = str(e)
        await save_session(session)


@app.post("/api/sessions")
async def create_session(
    background_tasks: BackgroundTasks,
    excel_file: UploadFile = File(...),
    goal: str = Form(...),
    constraints: str = Form(""),
    weights: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
):
    if not excel_file.filename or not excel_file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "excel_file должен быть .xlsx/.xls")

    session_id = str(uuid.uuid4())
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    excel_path = UPLOADS_DIR / f"{session_id}_{excel_file.filename}"
    with open(excel_path, "wb") as f:
        shutil.copyfileobj(excel_file.file, f)

    parsed_weights = RankingWeights.model_validate_json(weights) if weights else RankingWeights()

    session = SessionState(session_id, goal, constraints, parsed_weights, name=name)
    await save_session(session)

    background_tasks.add_task(
        _run_pipeline_background, session_id, str(excel_path), goal, constraints, parsed_weights
    )

    return {"session_id": session_id, "status": "running"}


@app.get("/api/sessions")
async def get_sessions(limit: int = 20, offset: int = 0):
    return await list_sessions(limit=limit, offset=offset)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    session = await load_session(session_id)
    if session is None:
        raise HTTPException(404, "сессия не найдена")
    return session.to_dict()


class RenameRequest(BaseModel):
    name: str


@app.post("/api/sessions/{session_id}/name")
async def rename_session(session_id: str, body: RenameRequest):
    session = await load_session(session_id)
    if session is None:
        raise HTTPException(404, "сессия не найдена")
    session.name = body.name
    await save_session(session)
    return session.to_dict()


class FeedbackRequest(BaseModel):
    action: Literal["approve", "reject", "skip"]
    comment: Optional[str] = None  # свободный комментарий эксперта — сохраняется на гипотезе


@app.post("/api/sessions/{session_id}/hypotheses/{hypothesis_id}/feedback")
async def feedback(session_id: str, hypothesis_id: str, body: FeedbackRequest):
    session = await load_session(session_id)
    if session is None:
        raise HTTPException(404, "сессия не найдена")
    for hyp in session.hypotheses:
        if hyp.id == hypothesis_id:
            hyp.status = _ACTION_TO_STATUS[body.action]
            if body.comment is not None:
                hyp.comment = body.comment
            await save_session(session)
            return hyp.model_dump(mode="json")
    raise HTTPException(404, "гипотеза не найдена")


def _load_hypotheses_db() -> dict:
    if config.HYPOTHESES_DB_PATH.exists():
        return json.loads(config.HYPOTHESES_DB_PATH.read_text(encoding="utf-8"))
    return {}


class RegenerateRequest(BaseModel):
    comment: str


@app.post("/api/sessions/{session_id}/hypotheses/{hypothesis_id}/regenerate")
async def regenerate_hypothesis(session_id: str, hypothesis_id: str, body: RegenerateRequest):
    """HITL: эксперт оставляет комментарий к гипотезе -> LLM переписывает её
    с учётом комментария -> заново проходит verify/rank/roadmap (содержание
    изменилось, старые оценки устарели). id гипотезы и место в списке не меняются."""
    session = await load_session(session_id)
    if session is None:
        raise HTTPException(404, "сессия не найдена")
    hyp = next((h for h in session.hypotheses if h.id == hypothesis_id), None)
    if hyp is None:
        raise HTTPException(404, "гипотеза не найдена")

    spec = await parse_target(session.goal, session.constraints)
    retrieval = await retrieve(hyp.statement, k=10)
    context_texts = [c.content for c in retrieval.chunks]

    revised = await revise_hypothesis(hyp, body.comment, retrieval)

    known_index = await build_known_hypotheses_index(_load_hypotheses_db())
    revised = await verify(revised, spec, known_index, context_texts)
    revised = await score_hypothesis(revised)
    revised.score = compute_weighted_score(revised, session.weights)
    revised.roadmap = await build_roadmap(revised)

    session.hypotheses = [revised if h.id == hypothesis_id else h for h in session.hypotheses]
    await save_session(session)
    return revised.model_dump(mode="json")


@app.post("/api/sessions/{session_id}/rerank")
async def rerank_session(session_id: str, weights: RankingWeights):
    session = await load_session(session_id)
    if session is None:
        raise HTTPException(404, "сессия не найдена")
    session.weights = weights
    session.hypotheses = rerank_hypotheses(session.hypotheses, weights)
    await save_session(session)
    return session.to_dict()


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str, format: Literal["csv", "json", "docx"] = "csv"):
    session = await load_session(session_id)
    if session is None:
        raise HTTPException(404, "сессия не найдена")

    if format == "csv":
        path = export_csv(session_id, session.hypotheses)
        media_type = "text/csv"
    elif format == "json":
        path = export_json(session_id, session.hypotheses)
        media_type = "application/json"
    else:
        path = export_docx(session_id, session.goal, session.hypotheses)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/api/sessions/{session_id}/hypotheses/{hypothesis_id}/graph", response_class=HTMLResponse)
async def hypothesis_graph(session_id: str, hypothesis_id: str):
    session = await load_session(session_id)
    if session is None:
        raise HTTPException(404, "сессия не найдена")
    hyp = next((h for h in session.hypotheses if h.id == hypothesis_id), None)
    if hyp is None:
        raise HTTPException(404, "гипотеза не найдена")

    result = await retrieve(hyp.statement, k=10)
    return render_graph_html(result)


@app.get("/api/debug/graph/stats")
async def debug_graph_stats():
    """У LightRAG в этой интеграции нет своего дашборда (используется как
    библиотека, не отдельный сервер) — быстрая проверка «что вообще в графе»."""
    return graph_stats()


@app.get("/api/debug/graph", response_class=HTMLResponse)
async def debug_graph_html():
    """Весь граф знаний целиком (не подграф одной гипотезы, как .../graph)."""
    return render_full_graph_html()


@app.get("/health")
async def health():
    return {"status": "ok"}
