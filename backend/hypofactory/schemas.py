"""Общие контракты данных между всеми модулями. Меняем только по договорённости всей команды."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    """Единый формат чанка документа для всей системы.

    Пришёл из ingestion-черновика сокомандника (Норникель/schemas/chunk.py) — принят
    как канонический контракт вместо более узкого Chunk. P1 кладёт чанки в
    corpus.jsonl, P2 индексирует их в LightRAG (file_paths=source_file при ainsert).
    """

    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_file: str = Field(..., description="Имя исходного файла")
    doc_type: Literal[
        "textbook_pdf", "tailings_excel", "diagram_image", "regulation_pdf", "hypotheses_docx"
    ]
    page_or_sheet: Union[int, str] = Field(..., description="Номер страницы или имя листа Excel")
    content: str = Field(..., description="Текстовое содержание чанка")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Метаданные: авторы, год, параметры и т.д.")
    created_at: datetime = Field(default_factory=datetime.now)


class LossFinding(BaseModel):
    """Находка Tails Analyzer: где сконцентрированы потери металла."""

    element: str  # "Элемент 28" / "Элемент 29"
    size_class: str  # например "-10", "-45 +20"
    mineral_form: str  # "Раскрытый Pnt/Cp", "Закрытый Pnt/Cp", ...
    recoverable: bool
    tons: Optional[float] = None
    share_of_losses: Optional[float] = None  # доля от всех потерь элемента, 0..1
    interpretation: str  # "переизмельчение/шламы", "недораскрытие сростков", ...


class TargetSpec(BaseModel):
    """Распарсенная цель пользователя + ограничения."""

    goal: str
    kpi: Optional[str] = None
    constraints: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    """Ответ retrieve() из LightRAG: контекст + графовые сущности/связи с цитированием."""

    chunks: list[DocumentChunk]
    entities: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)  # "A -[влияет на]-> B"


class SourceRef(BaseModel):
    source: str
    page: Optional[int] = None
    quote: Optional[str] = None


class HypothesisStatus(str, Enum):
    NEW = "new"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class RoadmapStep(BaseModel):
    step: str
    resources: Optional[str] = None
    success_criteria: Optional[str] = None


class Hypothesis(BaseModel):
    """Главный выходной объект пайплайна."""

    id: str
    statement: str  # проверяемая формулировка
    mechanism: str  # ожидаемый механизм влияния
    sources: list[SourceRef] = Field(default_factory=list)
    expected_effect: str  # влияние на целевой KPI
    related_findings: list[str] = Field(default_factory=list)  # какие LossFinding закрывает
    # оценки 1-5 от Ranker
    novelty: Optional[float] = None
    feasibility: Optional[float] = None
    impact: Optional[float] = None
    risk: Optional[float] = None
    score: Optional[float] = None  # взвешенная сумма с весами пользователя
    # verification
    constraint_ok: Optional[bool] = None
    already_tried: Optional[str] = None  # ссылка на похожую историческую гипотезу
    critic_verdict: Optional[str] = None
    status: HypothesisStatus = HypothesisStatus.NEW
    roadmap: list[RoadmapStep] = Field(default_factory=list)


class PipelineStatus(BaseModel):
    """Событие прогресса пайплайна (для API/UI)."""

    node: str  # "analyzer" / "retrieval" / "generator" / ...
    message: str
    done: bool = False


class EquipmentItem(BaseModel):
    """Единица оборудования, извлечённая из регламента/списка оборудования (vision-OCR)."""

    name: str
    type: Optional[str] = None
    parameters: Optional[str] = None


class EquipmentList(BaseModel):
    items: list[EquipmentItem] = Field(default_factory=list)


class RankingWeights(BaseModel):
    """Веса критериев ранжирования, задаваемые пользователем (режим экспертной настройки)."""

    novelty: float = 1.0
    feasibility: float = 1.0
    impact: float = 1.0
    risk: float = 1.0


# Контракт пайплайна (backend/hypofactory/pipeline/graph.py):
# def run_pipeline(excel_path, goal, constraints, weights) -> Iterator[PipelineStatus | list[Hypothesis]]
