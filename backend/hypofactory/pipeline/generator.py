"""Hypothesis Generator: findings + retrieval + few-shot реальных гипотез -> Hypothesis[].

Few-shot строится ТОЛЬКО из hypotheses_db.json (примеры 1-3) — Пример 4 (ТОФ)
держим held-out для eval (см. PLAN.md §4, ingestion/docx_hypotheses.py).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from hypofactory.llm.client import get_client
from hypofactory.schemas import Hypothesis, LossFinding, RetrievalResult, SourceRef, TargetSpec

GENERATOR_SYSTEM_PROMPT = """Ты — эксперт-исследователь в области обогащения полезных ископаемых \
(флотация, измельчение, классификация, реагентный режим).

На основе целевой задачи, находок анализа потерь металлов в хвостах, контекста из научной \
литературы/регламентов и примеров РЕАЛЬНЫХ гипотез, которые предлагали эксперты на других \
фабриках компании (мозговой штурм), сформулируй список НОВЫХ, конкретных, проверяемых в \
лаборатории или промышленных условиях гипотез.

Примеры ниже сгруппированы по технологическим сферам (классификация/грохочение/гидроциклоны, \
измельчение/дробление, флотация/реагентный режим, автоматизация/контроль параметров). То, что \
в одной сфере примеров больше, а в другой меньше — случайность (одни фабрики больше обсуждали \
одно, другие другое), а НЕ сигнал, что эта сфера важнее. Итоговый список гипотез должен \
охватывать РАЗНЫЕ сферы, насколько это оправдано находками анализа, а не концентрироваться \
только в одной: если находки показывают проблему и с крупностью класса, и с раскрытием \
сростков минерала, предложи гипотезы и про классификацию/разделение по крупности (грохота, \
гидроциклоны, классификаторы), и про измельчение/доизмельчение — а не только одно из двух.

Требования к каждой гипотезе:
- конкретное техническое изменение оборудования, режима или реагента (в стиле примеров, \
не общие слова вида "улучшить флотацию")
- явный ожидаемый механизм влияния на целевой показатель
- ссылки на источники из контекста: указывай file_path и короткую цитату (source_quotes \
в формате "file_path: цитата")
- НЕ дублируй дословно ни один из примеров реальных гипотез — это база для стиля, а не для копирования

Отвечай только на русском языке. Верни строго JSON по схеме — ключи JSON
(hypotheses, statement, mechanism, expected_effect, related_findings,
source_quotes) оставляй ровно такими, как в схеме, НЕ переводи их на русский —
переводить нужно только значения (текст самих гипотез)."""


class HypothesisDraft(BaseModel):
    statement: str
    mechanism: str
    expected_effect: str
    related_findings: list[str] = Field(default_factory=list)
    source_quotes: list[str] = Field(default_factory=list)


class HypothesisDraftList(BaseModel):
    # Без default_factory: модель иногда отвечает валидным JSON, но с
    # русскими ключами верхнего уровня ("гипотезы" вместо "hypotheses") —
    # с default_factory pydantic молча подставлял [] вместо ошибки валидации,
    # и repair-retry в acomplete_json ни разу не срабатывал (проверено на
    # реальной сессии: 8/8 узлов "done", но 0 гипотез на выходе). Обязательное
    # поле превращает это в настоящую ValidationError -> запускает repair.
    hypotheses: list[HypothesisDraft]


def _parse_source_quote(raw: str) -> SourceRef:
    if ":" in raw:
        source, quote = raw.split(":", 1)
        return SourceRef(source=source.strip(), quote=quote.strip())
    return SourceRef(source=raw.strip())


def _format_findings(findings: list[LossFinding]) -> str:
    lines = []
    for f in findings[:15]:
        share_pct = f"{(f.share_of_losses or 0) * 100:.1f}%"
        status = "ИЗВЛЕКАЕМО" if f.recoverable else "не извлекается текущей технологией"
        lines.append(
            f"- {f.element}, класс {f.size_class} мкм, форма «{f.mineral_form}», "
            f"{status}, {f.tons} т ({share_pct} потерь элемента): {f.interpretation}"
        )
    return "\n".join(lines)


def _format_context(retrieval: RetrievalResult) -> str:
    return "\n\n".join(f"[Источник: {c.source_file}]\n{c.content[:500]}" for c in retrieval.chunks[:10])


# Порядок и ключевые слова для группировки few-shot по сферам (см. docstring
# _format_few_shot: без этого модель неявно копирует пропорции тем в примерах).
_SPHERES: list[tuple[str, tuple[str, ...]]] = [
    ("Классификация/грохочение/гидроциклоны", ("классифика", "гидроциклон", "грохот", "сит", "насад")),
    ("Измельчение/дробление", ("мельниц", "футеровк", "дробилк", "измельч", "шар", "гал")),
    ("Флотация/реагентный режим", ("флотаци", "реагент", "пульп", "чан", "агитаци")),
    ("Автоматизация/контроль параметров", ("автоматиза", "контрол", "регулирован", "гранулометри")),
]
_OTHER_SPHERE = "Прочее"


def _classify_sphere(text: str) -> str:
    lower = text.lower()
    for sphere, keywords in _SPHERES:
        if any(kw in lower for kw in keywords):
            return sphere
    return _OTHER_SPHERE


def _format_few_shot(hypotheses_db: dict[str, list[str]]) -> str:
    """Группируем по ТЕХНОЛОГИЧЕСКОЙ СФЕРЕ, а не по фабрике: иначе модель неявно
    копирует пропорции тем в примерах (у КГМК/НОФ доминируют мельницы/флотация)
    и стабильно недогенерирует гипотезы про классификацию — эту асимметрию
    поймали на eval held-out Примера 4, где эксперты ТОФ наоборот в основном
    предлагали именно классификаторы/грохота (см. PLAN.md)."""
    by_sphere: dict[str, list[str]] = {}
    for factory, hyps in hypotheses_db.items():
        for h in hyps:
            by_sphere.setdefault(_classify_sphere(h), []).append(f"{h} (фабрика {factory})")

    order = [sphere for sphere, _ in _SPHERES] + [_OTHER_SPHERE]
    blocks = []
    for sphere in order:
        items = by_sphere.get(sphere)
        if not items:
            continue
        lines = "\n".join(f"- {h}" for h in items)
        blocks.append(f"### Сфера «{sphere}»:\n{lines}")
    return "\n\n".join(blocks)


async def generate_hypotheses(
    spec: TargetSpec,
    findings: list[LossFinding],
    retrieval: RetrievalResult,
    hypotheses_db: dict[str, list[str]],
    n: int = 10,
) -> list[Hypothesis]:
    client = get_client()

    prompt = f"""ЦЕЛЬ: {spec.goal}
KPI: {spec.kpi or "не указан явно"}
ОГРАНИЧЕНИЯ: {", ".join(spec.constraints) or "не указаны"}
ДОСТУПНОЕ ОБОРУДОВАНИЕ: {", ".join(spec.equipment) or "не указано"}

НАХОДКИ АНАЛИЗА ПОТЕРЬ (Tails Analyzer):
{_format_findings(findings)}

КОНТЕКСТ ИЗ ЛИТЕРАТУРЫ/РЕГЛАМЕНТОВ:
{_format_context(retrieval)}

{_format_few_shot(hypotheses_db)}

Сформулируй {n} гипотез."""

    draft_list = await client.acomplete_json(prompt, HypothesisDraftList, system_prompt=GENERATOR_SYSTEM_PROMPT)

    hypotheses = []
    for draft in draft_list.hypotheses[:n]:
        hypotheses.append(
            Hypothesis(
                id=str(uuid.uuid4()),
                statement=draft.statement,
                mechanism=draft.mechanism,
                sources=[_parse_source_quote(q) for q in draft.source_quotes],
                expected_effect=draft.expected_effect,
                related_findings=draft.related_findings,
            )
        )
    return hypotheses
