"""Общие контракты данных между всеми модулями. Меняем только по договорённости всей команды."""

from enum import Enum
from typing import Iterator, Optional

from pydantic import BaseModel, Field


class DocType(str, Enum):
    TEXTBOOK = "textbook"
    SCHEME_OCR = "scheme_ocr"
    REGULATION_OCR = "regulation_ocr"
    HYPOTHESES_DOC = "hypotheses_doc"


class Chunk(BaseModel):
    """Единица корпуса. P1 кладёт в corpus.jsonl, P2 индексирует в LightRAG."""

    id: str
    text: str
    source: str  # имя файла-источника
    page: Optional[int] = None
    doc_type: DocType


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
    """Ответ retrieve() из LightRAG: контекст + графовые сущности."""

    chunks: list[Chunk]
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
    """Событие прогресса для UI (P4)."""

    node: str  # "analyzer" / "retrieval" / "generator" / ...
    message: str
    done: bool = False


# Контракт P3 -> P4:
# def run_pipeline(excel_path, goal, constraints, weights) -> Iterator[PipelineStatus | list[Hypothesis]]
