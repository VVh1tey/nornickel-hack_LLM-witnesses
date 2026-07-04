"""E2E smoke-тест: полный LangGraph-пайплайн (все 8 узлов) на FakeLLM + реальном
Excel Примера 1, без сети. Проверяет форму выходов, а не качество генерации
(качество недостижимо без реальных ключей YandexGPT — см. README §Ограничения)."""

from __future__ import annotations

from pathlib import Path

import pytest

import hypofactory.llm.client as client_module
from hypofactory import config
from hypofactory.llm.client import FakeLLM
from hypofactory.pipeline.graph import run_pipeline

MATERIALS_ROOT = Path(__file__).resolve().parents[2] / "Задача 1. Фабрика гипотез" / "Задача 1"

EXPECTED_NODE_ORDER = [
    "analyzer",
    "target_spec",
    "query_builder",
    "retrieval",
    "generator",
    "verification",
    "ranker",
    "roadmap",
]


def _find_example_excel() -> Path | None:
    if not MATERIALS_ROOT.exists():
        return None
    for p in MATERIALS_ROOT.rglob("*.xlsx"):
        if "хвосты" in p.name.lower():
            return p
    return None


EXCEL_PATH = _find_example_excel()


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    # HYPOFACTORY_FAKE_LLM гарантирует is_fake_forced()=True независимо от
    # содержимого .env (у разработчика там могут быть настоящие ключи) — иначе
    # embeddings.warmup()/aembed() попытаются реально скачать/дёрнуть модель.
    monkeypatch.setenv("HYPOFACTORY_FAKE_LLM", "1")
    fake = FakeLLM()
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "get_client", lambda: fake)
    yield fake


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    """Не трогаем настоящие data/lightrag, data/processed разработчика."""
    monkeypatch.setattr(config, "LIGHTRAG_DIR", tmp_path / "lightrag")
    monkeypatch.setattr(config, "HYPOTHESES_DB_PATH", tmp_path / "hypotheses_db.json")


@pytest.mark.skipif(EXCEL_PATH is None, reason="раздаточные материалы хакатона не найдены рядом с репозиторием")
async def test_run_pipeline_smoke() -> None:
    statuses: list[str] = []
    final = None

    async for event in run_pipeline(
        str(EXCEL_PATH), goal="Снизить потери Элемента 28 с хвостами", constraints="Без остановки схемы"
    ):
        if isinstance(event, list):
            final = event
        else:
            statuses.append(event.node)

    assert statuses == EXPECTED_NODE_ORDER
    assert final is not None
    assert isinstance(final, list)
