"""Ranker: LLM-judge рубрика (новизна/реализуемость/эффект/риск) + взвешенная сумма.

Веса пользователь двигает слайдерами в UI (RankingWeights) — пересчёт итогового
score идёт БЕЗ повторного вызова LLM (compute_weighted_score), только оценки
1-5 от judge стоят одного вызова на гипотезу.
"""

from __future__ import annotations

from pydantic import BaseModel

from hypofactory.llm.client import get_client
from hypofactory.schemas import Hypothesis, RankingWeights

RUBRIC_SYSTEM_PROMPT = (
    "Ты эксперт-судья, оценивающий исследовательские гипотезы по обогащению руд "
    "цветных металлов. Оценивай строго и дифференцированно, избегай одинаковых "
    "оценок по всем критериям."
)

RUBRIC_PROMPT = """Оцени гипотезу по 4 критериям, шкала 1-5:
- novelty (новизна): 1=точь-в-точь как известные решения, 5=принципиально новый подход
- feasibility (реализуемость): 1=нереалистично с текущим оборудованием, 5=легко внедрить
- impact (потенциальный эффект на KPI): 1=незначительный эффект, 5=существенный эффект
- risk (риск): 1=низкий риск, 5=высокий риск (технический и экономический)

Гипотеза: {statement}
Механизм: {mechanism}
Ожидаемый эффект: {expected_effect}
Похожее уже пробовали: {already_tried}
Вердикт научного критика: {critic_verdict}

Верни JSON с оценками."""


class RankingScores(BaseModel):
    novelty: float
    feasibility: float
    impact: float
    risk: float


async def score_hypothesis(hyp: Hypothesis) -> Hypothesis:
    client = get_client()
    prompt = RUBRIC_PROMPT.format(
        statement=hyp.statement,
        mechanism=hyp.mechanism,
        expected_effect=hyp.expected_effect,
        already_tried=hyp.already_tried or "нет",
        critic_verdict=hyp.critic_verdict or "нет",
    )
    scores = await client.acomplete_json(prompt, RankingScores, system_prompt=RUBRIC_SYSTEM_PROMPT)
    hyp.novelty = scores.novelty
    hyp.feasibility = scores.feasibility
    hyp.impact = scores.impact
    hyp.risk = scores.risk
    return hyp


def compute_weighted_score(hyp: Hypothesis, weights: RankingWeights) -> float:
    novelty, feasibility, impact, risk = (
        hyp.novelty or 0,
        hyp.feasibility or 0,
        hyp.impact or 0,
        hyp.risk or 0,
    )
    total_weight = weights.novelty + weights.feasibility + weights.impact + weights.risk
    if not total_weight:
        return 0.0
    # риск инвертируем: выше риск -> ниже итоговый скор
    score = (
        weights.novelty * novelty
        + weights.feasibility * feasibility
        + weights.impact * impact
        + weights.risk * (6 - risk)
    ) / total_weight
    return round(score, 3)


async def rank(hypotheses: list[Hypothesis], weights: RankingWeights) -> list[Hypothesis]:
    for hyp in hypotheses:
        await score_hypothesis(hyp)
        hyp.score = compute_weighted_score(hyp, weights)
    hypotheses.sort(key=lambda h: h.score or 0, reverse=True)
    return hypotheses


def rerank(hypotheses: list[Hypothesis], weights: RankingWeights) -> list[Hypothesis]:
    """Пересортировка при смене весов пользователем — без обращения к LLM."""
    for hyp in hypotheses:
        hyp.score = compute_weighted_score(hyp, weights)
    hypotheses.sort(key=lambda h: h.score or 0, reverse=True)
    return hypotheses
