"""Единая точка вызова LLM для всей системы — два провайдера на выбор
(config.LLM_PROVIDER, см. .env): "ollama" (маленький Qwen локально, по
умолчанию — для отладки без ключей/прав Yandex) или "yandex" (YandexGPT через
yandex-ai-studio-sdk). Используется и в LangGraph-пайплайне, и как
llm_model_func в LightRAG, и в vision-OCR ingestion.

Если явно задан HYPOFACTORY_FAKE_LLM=1 — прозрачно переключается на FakeLLM с
детерминированными ответами (тесты). Если LLM_PROVIDER=yandex, но реальных
ключей нет (плейсхолдеры из .env.example) — тоже фолбэк на FakeLLM.

API yandex-ai-studio-sdk (проверено на пакете 0.22.0, см. PLAN.md):
    sdk = AsyncAIStudio(folder_id=..., auth=...)
    sdk.models.completions('yandexgpt').configure(temperature=..., response_format=...).run(text|messages)
    sdk.chat.completions('gemma-3-27b-it')  # единственная модель с поддержкой картинок
    result.text  # текст первой альтернативы

API Ollama (сервис в docker-compose.yml, модель тянется docker compose run --rm
ollama-pull): POST {OLLAMA_BASE_URL}/api/chat {model, messages, stream:false,
format:"json"?} -> {"message": {"role","content"}, ...}.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from hypofactory import config, tracing

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_PLACEHOLDER_FOLDER_ID = "b1g..."
_PLACEHOLDER_API_KEY = "AQVN..."


def has_real_credentials() -> bool:
    return (
        bool(config.YC_FOLDER_ID)
        and bool(config.YC_API_KEY)
        and config.YC_FOLDER_ID != _PLACEHOLDER_FOLDER_ID
        and config.YC_API_KEY != _PLACEHOLDER_API_KEY
    )


def is_fake_forced() -> bool:
    """Единственная причина уйти в FakeLLM независимо от провайдера — явный
    флаг (тесты, отладка без сети вообще)."""
    import os

    return os.getenv("HYPOFACTORY_FAKE_LLM") == "1"


def _strip_json_fences(text: str) -> str:
    """Некоторые модели оборачивают JSON в ```json ... ``` даже при
    format="json" — на всякий случай подчищаем перед валидацией."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")[1:]  # убираем открывающую строку ``` или ```json
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_text(result: Any) -> str:
    """Унифицирует чтение ответа: и completions, и chat.completions отдают .text
    на самом результате (проксируется на первую альтернативу), но подстрахуемся."""
    text = getattr(result, "text", None)
    if text is not None:
        return text
    try:
        return result[0].text
    except (TypeError, IndexError, AttributeError):
        return str(result)


def _cache_key(*parts: str) -> str:
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return h


def _cache_path(key: str) -> Path:
    config.LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.LLM_CACHE_DIR / f"{key}.json"


def _cache_get(key: str) -> Optional[str]:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["response"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _cache_set(key: str, response: str) -> None:
    path = _cache_path(key)
    try:
        path.write_text(json.dumps({"response": response}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.warning("Не удалось записать LLM-кэш в %s", path)


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think_tags(text: str) -> str:
    """Qwen3/3.5 умеют "thinking mode" — если рассуждения просочились прямо в
    message.content как <think>...</think> (а не в отдельное поле ответа
    Ollama), это ломает JSON-парсинг и просто мусорит текст гипотез. Дёшево
    подчищаем на всякий случай, даже если по факту не понадобится."""
    return _THINK_TAG_RE.sub("", text).strip()


_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힣]")


def _has_cjk(text: str) -> bool:
    """Qwen (родом от Alibaba) иногда переключается на китайский посреди
    ответа даже при явной инструкции "отвечай только на русском" в системном
    промпте — проверено на реальных прогонах (см. PLAN.md). Ловим постфактум
    и перегенерируем один раз с усиленной инструкцией, а не полагаемся только
    на промпт."""
    return bool(_CJK_RE.search(text))


def _retriable_exceptions() -> tuple[type[BaseException], ...]:
    try:
        from yandex_ai_studio_sdk import exceptions as yexc

        return (yexc.AIStudioError, yexc.AioRpcError)
    except ImportError:
        return (Exception,)


class FakeLLM:
    """Детерминированная заглушка: без сети, без ключей. Используется, если
    реальных credentials нет, а также в тестах (test_pipeline_smoke)."""

    async def acomplete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history_messages: Optional[list[dict]] = None,
        **kwargs: Any,
    ) -> str:
        return f"[FAKE-LLM ответ на: {prompt[:120]!r}]"

    def complete(self, prompt: str, system_prompt: Optional[str] = None, **kwargs: Any) -> str:
        return f"[FAKE-LLM ответ на: {prompt[:120]!r}]"

    async def acomplete_json(
        self,
        prompt: str,
        schema: type[T],
        system_prompt: Optional[str] = None,
    ) -> T:
        return _dummy_instance(schema)

    async def adescribe_image(
        self, image_bytes: bytes, prompt: str, mime_type: str = "image/png"
    ) -> str:
        return "[FAKE-LLM описание изображения: инференс недоступен без ключей]"


def _dummy_instance(schema: type[T]) -> T:
    """Строит минимально валидный экземпляр pydantic-схемы фиктивными значениями.
    Нужно, чтобы пайплайн был прогоняемым end-to-end без LLM (smoke-тесты)."""
    import enum

    def dummy_for(annotation: Any) -> Any:
        origin = getattr(annotation, "__origin__", None)
        if origin in (list, set, tuple):
            # ОДИН пример-элемент, не пустой список: иначе модель (особенно
            # локальная/маленькая через Ollama, которой это служит примером
            # формата в acomplete_json) не видит форму вложенного объекта и
            # пропускает поля (проверено: list[HypothesisDraft] с [] в
            # примере -> модель теряла expected_effect у каждой гипотезы).
            item_args = [a for a in getattr(annotation, "__args__", ()) if a is not type(None)]
            if not item_args:
                return []
            item = dummy_for(item_args[0])
            return [item] if item is not None else []
        if origin is dict:
            return {}
        if origin is not None:  # Optional[...] / Union[...]
            args = [a for a in getattr(annotation, "__args__", ()) if a is not type(None)]
            return dummy_for(args[0]) if args else None
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return _dummy_instance(annotation).model_dump()
        if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
            return next(iter(annotation)).value
        if annotation is str:
            return "fake"
        if annotation is int:
            return 0
        if annotation is float:
            return 0.0
        if annotation is bool:
            return False
        return None

    data = {name: dummy_for(field.annotation) for name, field in schema.model_fields.items()}
    return schema.model_validate(data)


class YandexLLMClient:
    """Обёртка над yandex-ai-studio-sdk. Изолирует всю систему от деталей SDK."""

    def __init__(self) -> None:
        from yandex_ai_studio_sdk import AIStudio, AsyncAIStudio

        self._async_sdk = AsyncAIStudio(folder_id=config.YC_FOLDER_ID, auth=config.YC_API_KEY)
        self._sync_sdk = AIStudio(folder_id=config.YC_FOLDER_ID, auth=config.YC_API_KEY)
        self._semaphore = asyncio.Semaphore(config.LLM_MAX_CONCURRENCY)

    def _messages(
        self, prompt: str, system_prompt: Optional[str], history_messages: Optional[list[dict]]
    ) -> list[dict]:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "text": system_prompt})
        if history_messages:
            messages.extend(history_messages)
        messages.append({"role": "user", "text": prompt})
        return messages

    async def acomplete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history_messages: Optional[list[dict]] = None,
        temperature: float = 0.3,
        **kwargs: Any,
    ) -> str:
        """Сигнатура совместима с llm_model_func LightRAG (prompt, system_prompt, history_messages, **kwargs)."""
        messages = self._messages(prompt, system_prompt, history_messages)
        cache_key = _cache_key(config.YC_MODEL, str(temperature), json.dumps(messages, ensure_ascii=False))
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        @retry(
            retry=retry_if_exception_type(_retriable_exceptions()),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        async def _call() -> str:
            async with self._semaphore:
                model = self._async_sdk.models.completions(config.YC_MODEL).configure(temperature=temperature)
                result = await model.run(messages)
                return _extract_text(result)

        with tracing.trace_generation(name="yandex.acomplete", model=config.YC_MODEL, input=messages) as gen:
            response = await _call()
            gen.update(output=response)
        _cache_set(cache_key, response)
        return response

    def complete(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.3, **kwargs: Any) -> str:
        messages = self._messages(prompt, system_prompt, None)
        cache_key = _cache_key(config.YC_MODEL, str(temperature), json.dumps(messages, ensure_ascii=False))
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        model = self._sync_sdk.models.completions(config.YC_MODEL).configure(temperature=temperature)
        result = model.run(messages)
        response = _extract_text(result)
        _cache_set(cache_key, response)
        return response

    async def acomplete_json(
        self,
        prompt: str,
        schema: type[T],
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
    ) -> T:
        """JSON-mode со строгой pydantic-валидацией и одним repair-циклом.

        Структурированный вывод у sdk.models.completions на момент написания SDK
        доступен только на model_version='rc', поэтому запрашиваем rc; но валидацию
        и повтор делаем в любом случае — так надёжнее, чем полагаться на режим.
        """
        messages = self._messages(prompt, system_prompt, None)

        async def _run(msgs: list[dict]) -> str:
            async with self._semaphore:
                model = self._async_sdk.models.completions(
                    config.YC_MODEL, model_version="rc"
                ).configure(temperature=temperature, response_format=schema)
                result = await model.run(msgs)
                return _extract_text(result)

        with tracing.trace_generation(name="yandex.acomplete_json", model=config.YC_MODEL, input=messages) as gen:
            raw = await _run(messages)
            try:
                result = schema.model_validate_json(raw)
            except (ValidationError, json.JSONDecodeError) as first_error:
                repair_messages = messages + [
                    {"role": "assistant", "text": raw},
                    {
                        "role": "user",
                        "text": (
                            "Ответ не прошёл валидацию по JSON-схеме. Ошибка: "
                            f"{first_error}. Верни ИСПРАВЛЕННЫЙ валидный JSON строго по схеме, без пояснений."
                        ),
                    },
                ]
                raw_repaired = await _run(repair_messages)
                result = schema.model_validate_json(raw_repaired)
            gen.update(output=raw)
        return result

    async def adescribe_image(self, image_bytes: bytes, prompt: str, mime_type: str = "image/png") -> str:
        import base64

        b64 = base64.b64encode(image_bytes).decode("utf-8")

        @retry(
            retry=retry_if_exception_type(_retriable_exceptions()),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        async def _call() -> str:
            async with self._semaphore:
                model = self._async_sdk.chat.completions(config.YC_VISION_MODEL)
                request = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                        ],
                    }
                ]
                result = await model.run(request)
                return _extract_text(result)

        with tracing.trace_generation(
            name="yandex.adescribe_image", model=config.YC_VISION_MODEL, input=prompt
        ) as gen:
            response = await _call()
            gen.update(output=response)
        return response


class OllamaLLMClient:
    """Локальный LLM через Ollama (docker-compose: сервис `ollama` + разовая
    загрузка модели `docker compose run --rm ollama-pull`). Тот же интерфейс,
    что и YandexLLMClient — вызывающий код (LangGraph, LightRAG, vision-OCR) не
    знает, какой бэкенд используется. Модель (qwen2.5:7b по умолчанию, GPU
    через docker-compose deploy.resources) — vision не поддерживает,
    adescribe_image отдаёт заглушку."""

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(config.LLM_MAX_CONCURRENCY)

    def _messages(
        self, prompt: str, system_prompt: Optional[str], history_messages: Optional[list[dict]]
    ) -> list[dict]:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history_messages:
            messages.extend(history_messages)
        messages.append({"role": "user", "content": prompt})
        return messages

    @retry(
        # TimeoutException НЕ ретраим: маленькая CPU-модель может быть просто
        # медленной (не сломанной) на длинных чанках — ретрай только сбрасывает
        # прогресс и удлиняет ожидание. Ретраим только реально транзиентные
        # сбои (обрыв соединения, 5xx).
        retry=retry_if_exception_type((httpx.ConnectError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _chat_raw(self, messages: list[dict], *, json_mode: bool, temperature: float) -> str:
        payload: dict[str, Any] = {
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"

        # Таймаут щедрый: маленькие модели на CPU (~10-15 ток/с) на длинных
        # чанках/промптах LightRAG могут генерировать несколько минут.
        async with self._semaphore:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as http_client:
                response = await http_client.post(f"{config.OLLAMA_BASE_URL}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
        return _strip_think_tags(data["message"]["content"])

    async def _chat(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.3) -> str:
        cache_key = _cache_key(
            config.OLLAMA_MODEL, str(json_mode), str(temperature), json.dumps(messages, ensure_ascii=False)
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        with tracing.trace_generation(name="ollama.chat", model=config.OLLAMA_MODEL, input=messages) as gen:
            text = await self._chat_raw(messages, json_mode=json_mode, temperature=temperature)

            if _has_cjk(text):
                # Промпт с "отвечай только на русском" не спасает надёжно — ловим
                # постфактум и перегенерируем один раз с усиленной инструкцией.
                retry_messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "В ответе выше есть текст не на русском языке — это ошибка. "
                            "Повтори ответ ЗАНОВО полностью на русском языке, не переключаясь "
                            "на другой язык ни в одном слове."
                        ),
                    },
                ]
                retried = await self._chat_raw(retry_messages, json_mode=json_mode, temperature=temperature)
                if not _has_cjk(retried):
                    text = retried

            gen.update(output=text)

        _cache_set(cache_key, text)
        return text

    async def acomplete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history_messages: Optional[list[dict]] = None,
        temperature: float = 0.3,
        **kwargs: Any,
    ) -> str:
        messages = self._messages(prompt, system_prompt, history_messages)
        return await self._chat(messages, temperature=temperature)

    def complete(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.3, **kwargs: Any) -> str:
        return asyncio.run(self.acomplete(prompt, system_prompt=system_prompt, temperature=temperature))

    async def acomplete_json(
        self,
        prompt: str,
        schema: type[T],
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
    ) -> T:
        # Дамп полной JSON-схемы (properties/title/required/...) путает модели
        # среднего размера: они начинают "эхом" повторять мета-структуру схемы
        # вместо данных (проверено на qwen2.5:7b — с примером вместо схемы тот
        # же промпт отработал верно). Пример-заготовка с фиктивными значениями
        # интуитивнее для маленьких моделей, чем метаописание.
        example = _dummy_instance(schema).model_dump_json()
        json_system = (
            f"{system_prompt}\n\n" if system_prompt else ""
        ) + (
            "Отвечай СТРОГО валидным JSON, без пояснений и без markdown-разметки, "
            f"в точности такой структуры (это только пример формата, не значения для копирования):\n{example}"
        )
        messages = self._messages(prompt, json_system, None)

        raw = await self._chat(messages, json_mode=True, temperature=temperature)
        try:
            return schema.model_validate_json(_strip_json_fences(raw))
        except (ValidationError, json.JSONDecodeError) as first_error:
            repair_messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "Ответ не прошёл валидацию по JSON-схеме. Ошибка: "
                        f"{first_error}. Верни ИСПРАВЛЕННЫЙ валидный JSON строго по схеме, без пояснений."
                    ),
                },
            ]
            raw_repaired = await self._chat(repair_messages, json_mode=True, temperature=temperature)
            return schema.model_validate_json(_strip_json_fences(raw_repaired))

    async def adescribe_image(self, image_bytes: bytes, prompt: str, mime_type: str = "image/png") -> str:
        return "[Ollama: маленькая текстовая модель без vision — для описания схем нужен LLM_PROVIDER=yandex]"


_client: Optional[Any] = None


def get_client() -> Any:
    """Синглтон клиента: выбирает провайдер по config.LLM_PROVIDER, либо FakeLLM."""
    global _client
    if _client is None:
        if is_fake_forced():
            logger.warning("LLM: HYPOFACTORY_FAKE_LLM=1 — используется FakeLLM")
            _client = FakeLLM()
        elif config.LLM_PROVIDER == "ollama":
            _client = OllamaLLMClient()
        elif has_real_credentials():
            _client = YandexLLMClient()
        else:
            logger.warning("LLM_PROVIDER=yandex, но реальных ключей нет — используется FakeLLM")
            _client = FakeLLM()
    return _client
