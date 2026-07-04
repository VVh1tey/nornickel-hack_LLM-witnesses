"""Roadmap generator: топ-гипотезы -> шаги проверки, ресурсы, критерии успеха."""

from __future__ import annotations

from pydantic import BaseModel

from hypofactory import config
from hypofactory.domain_profile import get_profile
from hypofactory.llm.client import get_client
from hypofactory.schemas import Hypothesis, RoadmapStep

_PROFILE = get_profile(config.DOMAIN_PROFILE)

ROADMAP_SYSTEM_PROMPT = (
    f"Ты — {_PROFILE.expert_role}, планируешь программу лабораторной/промышленной "
    "верификации исследовательской гипотезы. Отвечай только на русском языке."
)

ROADMAP_PROMPT = """Гипотеза: {statement}
Механизм: {mechanism}
Ожидаемый эффект: {expected_effect}

Составь дорожную карту проверки гипотезы: 3-5 последовательных шагов
(лабораторные и/или промышленные эксперименты), для каждого шага укажи
необходимые ресурсы, критерий успеха/провала и оценку длительности этого
шага в днях (duration_days) — целое число, реалистичная оценка для
лабораторного/промышленного эксперимента такого масштаба (например 3-5 для
лабораторного теста, 14-30 для промышленных испытаний)."""


class RoadmapDraft(BaseModel):
    # Без default_factory — иначе модель, ответив валидным JSON с русским
    # ключом верхнего уровня вместо "steps", молча проходит валидацию с
    # пустым списком вместо запуска repair-retry (см. тот же баг и фикс в
    # generator.py: HypothesisDraftList).
    steps: list[RoadmapStep]


async def build_roadmap(hyp: Hypothesis) -> list[RoadmapStep]:
    client = get_client()
    prompt = ROADMAP_PROMPT.format(
        statement=hyp.statement, mechanism=hyp.mechanism, expected_effect=hyp.expected_effect
    )
    draft = await client.acomplete_json(prompt, RoadmapDraft, system_prompt=ROADMAP_SYSTEM_PROMPT)
    return draft.steps
