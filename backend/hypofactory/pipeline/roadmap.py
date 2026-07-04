"""Roadmap generator: топ-гипотезы -> шаги проверки, ресурсы, критерии успеха."""

from __future__ import annotations

from pydantic import BaseModel, Field

from hypofactory.llm.client import get_client
from hypofactory.schemas import Hypothesis, RoadmapStep

ROADMAP_SYSTEM_PROMPT = (
    "Ты планируешь программу лабораторной/промышленной верификации исследовательской "
    "гипотезы. Отвечай только на русском языке."
)

ROADMAP_PROMPT = """Гипотеза: {statement}
Механизм: {mechanism}
Ожидаемый эффект: {expected_effect}

Составь дорожную карту проверки гипотезы: 3-5 последовательных шагов
(лабораторные и/или промышленные эксперименты), для каждого шага укажи
необходимые ресурсы и критерий успеха/провала."""


class RoadmapDraft(BaseModel):
    steps: list[RoadmapStep] = Field(default_factory=list)


async def build_roadmap(hyp: Hypothesis) -> list[RoadmapStep]:
    client = get_client()
    prompt = ROADMAP_PROMPT.format(
        statement=hyp.statement, mechanism=hyp.mechanism, expected_effect=hyp.expected_effect
    )
    draft = await client.acomplete_json(prompt, RoadmapDraft, system_prompt=ROADMAP_SYSTEM_PROMPT)
    return draft.steps
