"""Hypothesis Generator: findings + retrieval + few-shot реальных гипотез -> Hypothesis[].

Few-shot строится из hypotheses_db.json (примеры 1-3, статичный историч.
набор — Пример 4/ТОФ держим held-out для eval, см. PLAN.md §4) ПЛЮС растущий
пул одобренных экспертами гипотез (feedback_learning.py). Из объединённого
пула в промпт попадают не ВСЕ примеры, а top-K, отобранные retrieval'ом по
релевантности текущей цели (см. _retrieve_fewshot) — иначе с каждым approve
промпт рос бы неограниченно.
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field

from hypofactory import config
from hypofactory.domain_profile import get_profile
from hypofactory.llm.client import get_client
from hypofactory.llm.embeddings import aembed, cached_embed_texts, cosine_similarity
from hypofactory.pipeline.feedback_learning import load_approved_pool
from hypofactory.schemas import Hypothesis, LossFinding, RetrievalResult, SourceRef, TargetSpec

FEWSHOT_TOP_K = 15
FEWSHOT_CACHE_PATH = config.PROCESSED_DIR / "fewshot_embeddings_cache.npz"

_PROFILE = get_profile(config.DOMAIN_PROFILE)

GENERATOR_SYSTEM_PROMPT = f"""Ты — {_PROFILE.expert_role}.

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


def _classify_sphere(text: str) -> str:
    """Ключевые слова сфер — из профиля домена (domain_profile.py), не
    захардкожены здесь: без этого модель неявно копирует пропорции тем в
    примерах вместо разнообразия (см. docstring _format_few_shot)."""
    lower = text.lower()
    for sphere, keywords in _PROFILE.spheres:
        if any(kw in lower for kw in keywords):
            return sphere
    return _PROFILE.other_sphere


def _load_hypotheses_db() -> dict[str, list[str]]:
    if config.HYPOTHESES_DB_PATH.exists():
        return json.loads(config.HYPOTHESES_DB_PATH.read_text(encoding="utf-8"))
    return {}


def _all_fewshot_entries() -> list[dict]:
    """Статичный историч. набор (hypotheses_db.json, по фабрикам) + растущий
    пул одобренных экспертами гипотез (feedback_learning.py) — единый пул-кандидат
    для retrieval, а не то, что целиком уходит в промпт."""
    entries = []
    for factory, hyps in _load_hypotheses_db().items():
        for h in hyps:
            entries.append({"statement": h, "source": f"фабрика {factory}"})
    for p in load_approved_pool():
        entries.append({"statement": p["statement"], "source": "одобрено экспертом ранее"})
    return entries


async def _retrieve_fewshot(spec: TargetSpec, findings: list[LossFinding], top_k: int = FEWSHOT_TOP_K) -> list[dict]:
    """top-K релевантных примеров из объединённого пула (история + одобренное),
    а не весь пул целиком — иначе промпт рос бы неограниченно с каждым approve
    (см. docstring модуля). Релевантность — косинус к цели+находкам, не к
    полному тексту промпта."""
    entries = _all_fewshot_entries()
    if not entries:
        return []
    if len(entries) <= top_k:
        return entries

    texts = [e["statement"] for e in entries]
    embeddings = await cached_embed_texts(texts, FEWSHOT_CACHE_PATH)

    query_text = spec.goal + "\n" + "\n".join(f.interpretation for f in findings[:5])
    query_emb = await aembed([query_text], context="query")
    sims = cosine_similarity(query_emb, embeddings)[0]
    top_idx = sims.argsort()[::-1][:top_k]
    return [entries[i] for i in top_idx]


def _format_few_shot(entries: list[dict]) -> str:
    """Группируем по ТЕХНОЛОГИЧЕСКОЙ СФЕРЕ, а не по источнику: иначе модель неявно
    копирует пропорции тем в примерах (у КГМК/НОФ доминируют мельницы/флотация)
    и стабильно недогенерирует гипотезы про классификацию — эту асимметрию
    поймали на eval held-out Примера 4, где эксперты ТОФ наоборот в основном
    предлагали именно классификаторы/грохота (см. PLAN.md). entries — уже
    отобранный retrieval'ом топ-K, а не весь пул (см. _retrieve_fewshot)."""
    by_sphere: dict[str, list[str]] = {}
    for e in entries:
        by_sphere.setdefault(_classify_sphere(e["statement"]), []).append(f"{e['statement']} ({e['source']})")

    order = [sphere for sphere, _ in _PROFILE.spheres] + [_PROFILE.other_sphere]
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
    n: int = 10,
) -> list[Hypothesis]:
    client = get_client()
    fewshot_entries = await _retrieve_fewshot(spec, findings)

    prompt = f"""ЦЕЛЬ: {spec.goal}
KPI: {spec.kpi or "не указан явно"}
ОГРАНИЧЕНИЯ: {", ".join(spec.constraints) or "не указаны"}
ДОСТУПНОЕ ОБОРУДОВАНИЕ: {", ".join(spec.equipment) or "не указано"}

НАХОДКИ АНАЛИЗА ПОТЕРЬ (Tails Analyzer):
{_format_findings(findings)}

КОНТЕКСТ ИЗ ЛИТЕРАТУРЫ/РЕГЛАМЕНТОВ:
{_format_context(retrieval)}

{_format_few_shot(fewshot_entries)}

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


REVISE_SYSTEM_PROMPT = (
    f"Ты — {_PROFILE.expert_role}. "
    "Тебе дают гипотезу и комментарий эксперта-практика с производства — "
    "перепиши гипотезу с учётом этого комментария, сохраняя тот же формат "
    "(конкретное техническое изменение + механизм + ожидаемый эффект). "
    "Отвечай только на русском языке. Верни строго JSON по схеме."
)

REVISE_PROMPT = """Исходная гипотеза: {statement}
Механизм: {mechanism}
Ожидаемый эффект: {expected_effect}

Комментарий эксперта: {comment}

КОНТЕКСТ ИЗ ЛИТЕРАТУРЫ/РЕГЛАМЕНТОВ:
{context}

Перепиши гипотезу с учётом комментария эксперта."""


class HypothesisRevision(BaseModel):
    statement: str
    mechanism: str
    expected_effect: str


async def revise_hypothesis(hyp: Hypothesis, comment: str, retrieval: RetrievalResult) -> Hypothesis:
    """Перегенерация ОДНОЙ гипотезы с учётом свободного комментария эксперта
    (HITL) — id и источники сохраняются, verify/rank/roadmap запускаются
    заново вызывающим кодом (api/app.py), т.к. содержание изменилось."""
    client = get_client()
    prompt = REVISE_PROMPT.format(
        statement=hyp.statement,
        mechanism=hyp.mechanism,
        expected_effect=hyp.expected_effect,
        comment=comment,
        context=_format_context(retrieval),
    )
    revision = await client.acomplete_json(prompt, HypothesisRevision, system_prompt=REVISE_SYSTEM_PROMPT)
    return Hypothesis(
        id=hyp.id,
        statement=revision.statement,
        mechanism=revision.mechanism,
        expected_effect=revision.expected_effect,
        sources=hyp.sources,
        related_findings=hyp.related_findings,
        comment=comment,
    )
