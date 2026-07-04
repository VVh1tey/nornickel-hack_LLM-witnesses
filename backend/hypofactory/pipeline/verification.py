"""Verification: constraint check + «уже пробовали» (similarity) + LLM-критик физики."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel

from hypofactory import config
from hypofactory.domain_profile import get_profile
from hypofactory.llm.client import get_client
from hypofactory.llm.embeddings import aembed, cosine_similarity
from hypofactory.schemas import Hypothesis, TargetSpec

# Ниже этого даже не спрашиваем LLM — темы точно разные, экономим вызов.
ALREADY_TRIED_CANDIDATE_THRESHOLD = 0.35

_PROFILE = get_profile(config.DOMAIN_PROFILE)


class ConstraintVerdict(BaseModel):
    ok: bool
    reason: str


class PhysicsVerdict(BaseModel):
    plausible: bool
    reason: str


class AlreadyTriedVerdict(BaseModel):
    same_idea: bool
    reason: str


@dataclass
class KnownHypothesesIndex:
    """Предвычисленные эмбеддинги исторических гипотез — считаем ОДИН раз за
    прогон пайплайна (не на каждую гипотезу!), иначе на каждую верификацию
    уйдёт лишний сетевой вызов эмбеддинга на весь список известных гипотез."""

    known: list[tuple[str, str]]  # (фабрика, текст гипотезы)
    embeddings: np.ndarray


async def build_known_hypotheses_index(hypotheses_db: dict[str, list[str]]) -> KnownHypothesesIndex:
    known = [(factory, h) for factory, hyps in hypotheses_db.items() for h in hyps]
    if not known:
        return KnownHypothesesIndex(known=[], embeddings=np.zeros((0, 0), dtype=np.float32))
    # исторические гипотезы экспертов индексируем как "документы" (асимметрично)
    embeddings = await aembed([h for _, h in known], context="document")
    return KnownHypothesesIndex(known=known, embeddings=embeddings)


async def check_already_tried(hyp: Hypothesis, index: KnownHypothesesIndex) -> str | None:
    """Гибридная проверка: эмбеддинг находит ближайшего кандидата (дёшево),
    LLM подтверждает, та же ли это идея по сути (надёжнее чистого косинуса).

    Чистый порог косинуса ненадёжен здесь: сгенерированная гипотеза обычно
    длиннее исторической (добавляет класс крупности/минеральную форму), из-за
    чего сходство размывается даже при полном совпадении сути идеи —
    проверено на реальном прогоне (см. PLAN.md)."""
    if not index.known:
        return None

    # гипотеза-кандидат — это "запрос" к индексу исторических гипотез
    hyp_emb = await aembed([hyp.statement], context="query")
    sims = cosine_similarity(hyp_emb, index.embeddings)[0]
    best_idx = int(np.argmax(sims))
    if sims[best_idx] < ALREADY_TRIED_CANDIDATE_THRESHOLD:
        return None

    factory, text = index.known[best_idx]
    client = get_client()
    verdict = await client.acomplete_json(
        f"""Гипотеза-кандидат: {hyp.statement}
Историческая гипотеза (фабрика {factory}): {text}

Это по сути одна и та же идея (даже если кандидат добавляет детали вроде
класса крупности или минеральной формы, которых нет в исторической
формулировке), или это разные идеи?""",
        AlreadyTriedVerdict,
        system_prompt=(
            f"Ты — {_PROFILE.expert_role}, сравниваешь исследовательские гипотезы "
            "на предмет дублирования сути идеи, а не дословного совпадения текста. "
            "Отвечай только на русском языке."
        ),
    )
    if verdict.same_idea:
        return f"Похоже на гипотезу фабрики {factory}: «{text}» ({verdict.reason})"
    return None


async def check_constraints(hyp: Hypothesis, spec: TargetSpec) -> tuple[bool, str]:
    client = get_client()
    prompt = f"""Гипотеза: {hyp.statement}
Механизм: {hyp.mechanism}
Ограничения задачи: {", ".join(spec.constraints) or "нет явных ограничений"}
Доступное оборудование: {", ".join(spec.equipment) or "не указано"}

Не противоречит ли гипотеза перечисленным ограничениям и доступному оборудованию?"""
    verdict = await client.acomplete_json(
        prompt,
        ConstraintVerdict,
        system_prompt=(
            f"Ты — {_PROFILE.expert_role}, проверяешь техническую реализуемость "
            "гипотез относительно заданных ограничений. Отвечай только на русском языке."
        ),
    )
    return verdict.ok, verdict.reason


async def check_physics(hyp: Hypothesis, context_chunks: list[str]) -> str:
    client = get_client()
    context = "\n".join(context_chunks[:5])
    prompt = f"""Гипотеза: {hyp.statement}
Механизм: {hyp.mechanism}
Контекст из литературы:
{context}

Правдоподобен ли описанный механизм с точки зрения физики/химии процесса? Кратко обоснуй."""
    verdict = await client.acomplete_json(
        prompt,
        PhysicsVerdict,
        system_prompt=f"Ты научный критик, {_PROFILE.expert_role}. Отвечай только на русском языке.",
    )
    verdict_label = "ПРАВДОПОДОБНО" if verdict.plausible else "СОМНИТЕЛЬНО"
    return f"{verdict_label}: {verdict.reason}"


async def verify(
    hyp: Hypothesis,
    spec: TargetSpec,
    known_index: KnownHypothesesIndex,
    context_chunks: list[str],
) -> Hypothesis:
    hyp.already_tried = await check_already_tried(hyp, known_index)
    hyp.constraint_ok, reason = await check_constraints(hyp, spec)
    hyp.critic_verdict = await check_physics(hyp, context_chunks)
    if not hyp.constraint_ok:
        hyp.critic_verdict = f"[Нарушение ограничений: {reason}] {hyp.critic_verdict}"
    return hyp
