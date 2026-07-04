"""Трейс вызовов LLM через self-hosted Langfuse (docker-compose.yml, сервисы
langfuse-*, UI на http://localhost:3000). Два уровня трейсинга:

1. Узлы пайплайна (analyzer/generator/verification/...) — через
   `get_langchain_handler()`, передаётся в LangGraph как
   `config={"callbacks": [...]}` (см. pipeline/graph.py). Работает "из коробки"
   — LangGraph сам эмитит callback-события на каждый узел.
2. Отдельные вызовы LLM внутри узлов (llm/client.py) — вручную, через
   `trace_generation()`: наш клиент вызывает Ollama/Yandex напрямую (не через
   LangChain LLM-обёртку), поэтому автоматический трейсинг узлов НЕ видит
   содержимое отдельных запросов/ответов — это даёт полную вложенную картину.

Если LANGFUSE_PUBLIC_KEY/SECRET_KEY не заданы, или Langfuse недоступен —
трейсинг no-op: наблюдаемость никогда не должна ронять генерацию гипотез.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from hypofactory import config

logger = logging.getLogger(__name__)

_enabled = bool(config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY)


class _NullGeneration:
    def update(self, **_kwargs: Any) -> None:
        pass


def get_langchain_handler() -> Optional[Any]:
    """CallbackHandler для config={"callbacks": [...]} в LangGraph — трейсит
    узлы пайплайна как span'ы. None, если Langfuse не настроен/недоступен."""
    if not _enabled:
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception:
        logger.warning("Langfuse CallbackHandler недоступен — трейсинг узлов пайплайна отключён", exc_info=True)
        return None


@contextmanager
def trace_generation(*, name: str, model: str, input: Any) -> Iterator[Any]:
    """Оборачивает один вызов LLM как generation-спан в Langfuse.
    Использование:
        with trace_generation(name="...", model=..., input=messages) as gen:
            response = await ...
            gen.update(output=response)

    ВАЖНО: ошибки самого вызова LLM (например httpx.ReadTimeout — у Ollama он
    намеренно не ретраится, см. client.py) должны свободно пролетать наружу.
    Раньше `yield` был внутри `try/except Exception`, из-за чего исключение
    вызывающего кода ловилось ЗДЕСЬ и код пытался yield'нуть второй раз —
    Python на этом падает с "generator didn't stop after throw()". Поэтому
    setup (может упасть, если Langfuse недоступен) и использование (может
    упасть по вине вызывающего кода, это НЕ наша ошибка) разделены."""
    if not _enabled:
        yield _NullGeneration()
        return

    try:
        from langfuse import get_client

        client = get_client()
        cm = client.start_as_current_observation(as_type="generation", name=name, model=model, input=input)
        gen = cm.__enter__()
    except Exception:
        logger.warning("Langfuse-трейсинг вызова %s недоступен, продолжаем без него", name, exc_info=True)
        yield _NullGeneration()
        return

    try:
        yield gen
    except BaseException:
        cm.__exit__(*sys.exc_info())
        raise
    else:
        cm.__exit__(None, None, None)
