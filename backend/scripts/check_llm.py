"""Быстрая проверка подключения к активному LLM-провайдеру (config.LLM_PROVIDER)
ДО индексации/пайплайна: один текстовый вызов + один JSON-вызов. Дёшево и быстро —
гонять перед index_lightrag.py, чтобы не тратить время/квоту на сломанной настройке.
    uv run python backend/scripts/check_llm.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel

from hypofactory import config
from hypofactory.llm.client import FakeLLM, get_client


class Ping(BaseModel):
    answer: str


async def main() -> None:
    print(f"LLM_PROVIDER={config.LLM_PROVIDER}")
    client = get_client()
    print(f"Активный клиент: {type(client).__name__}")

    if isinstance(client, FakeLLM):
        print(
            "Используется FakeLLM — либо HYPOFACTORY_FAKE_LLM=1, либо "
            "LLM_PROVIDER=yandex без реальных ключей. Проверять нечего."
        )
        return

    print("1) простая генерация...")
    text = await client.acomplete("Скажи одним словом: работает ли соединение?")
    print("   ->", text)

    print("2) JSON-режим...")
    obj = await client.acomplete_json("Ответь JSON-ом с полем answer='ok'", Ping)
    print("   ->", obj)

    print("Готово — провайдер отвечает.")


if __name__ == "__main__":
    asyncio.run(main())
