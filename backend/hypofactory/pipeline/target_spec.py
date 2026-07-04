"""Цель пользователя (текст) + ограничения -> TargetSpec (Pydantic)."""

from __future__ import annotations

from hypofactory import config
from hypofactory.domain_profile import get_profile
from hypofactory.llm.client import get_client
from hypofactory.schemas import TargetSpec

_PROFILE = get_profile(config.DOMAIN_PROFILE)

SYSTEM_PROMPT = (
    f"Ты помощник по формулировке исследовательских задач для {_PROFILE.expert_role}. "
    "Извлеки из запроса пользователя: цель (goal), ключевой KPI, если он назван "
    "явно или подразумевается (kpi), список ограничений (constraints) и "
    "упомянутое оборудование (equipment). Отвечай только на русском языке. "
    "Верни строго JSON по схеме."
)


async def parse_target(
    goal_text: str,
    constraints_text: str = "",
    equipment_hint: list[str] | None = None,
) -> TargetSpec:
    client = get_client()
    prompt = (
        f"Цель пользователя: {goal_text}\n"
        f"Ограничения (как есть, могут быть неструктурированными): {constraints_text or '(не указаны)'}"
    )
    spec = await client.acomplete_json(prompt, TargetSpec, system_prompt=SYSTEM_PROMPT)
    if equipment_hint:
        spec.equipment = list(dict.fromkeys([*spec.equipment, *equipment_hint]))
    return spec
