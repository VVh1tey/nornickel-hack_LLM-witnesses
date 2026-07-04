"""Оценка качества на held-out Примере 4 (ТОФ) — единственном, чьи реальные
гипотезы экспертов НЕ участвуют ни в few-shot генератора, ни в базе «уже
пробовали» (см. build_corpus.py: docx Примера 4 идёт только в goldset).

Три вещи:
1. Coverage vs эксперты — сколько из 8 реальных гипотез экспертов ТОФ
   воспроизвёл пайплайн (LLM-judge на смысловое совпадение, не дословное).
2. GEval (deepeval) — конкретность и проверяемость каждой сгенерированной
   гипотезы: не общие слова, а формулировка, которую можно проверить в лаборатории.
3. Оценки ranker (novelty/feasibility/impact/risk), уже посчитанные пайплайном —
   просто печатаются для полноты картины.

deepeval гоняется на активном LLM-провайдере (LLM_PROVIDER=ollama/yandex) через
обёртку DeepEvalClientWrapper поверх нашего llm/client.py — отдельный API-ключ
для судьи не нужен.

Запуск:
    uv run python backend/eval/run_eval.py
Результат — на экран, в backend/eval/last_report.json и в наглядный
backend/eval/last_report.html (открыть в браузере)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # для локального render_report.py

from deepeval.metrics import GEval
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from pydantic import BaseModel

from hypofactory import config
from hypofactory.llm.client import get_client
from hypofactory.pipeline.graph import run_pipeline
from hypofactory.schemas import Hypothesis

GOLDSET_PATH = config.ROOT / "backend" / "eval" / "goldsets" / "tof_expected.json"
REPORT_PATH = config.ROOT / "backend" / "eval" / "last_report.json"
MATERIALS_ROOT = config.ROOT / "Задача 1. Фабрика гипотез" / "Задача 1"


class DeepEvalClientWrapper(DeepEvalBaseLLM):
    """Оборачивает наш llm/client.py (какой бы провайдер ни был активен —
    Ollama/Yandex/FakeLLM) для deepeval GEval — отдельный ключ для судьи не нужен."""

    def load_model(self):
        return get_client()

    def get_model_name(self) -> str:
        return config.OLLAMA_MODEL if config.LLM_PROVIDER == "ollama" else config.YC_MODEL

    async def a_generate(self, prompt: str, schema: Optional[type] = None, **kwargs):
        client = self.model
        if schema is not None:
            return await client.acomplete_json(prompt, schema)
        return await client.acomplete(prompt)

    def generate(self, prompt: str, schema: Optional[type] = None, **kwargs):
        return asyncio.run(self.a_generate(prompt, schema=schema, **kwargs))


class CoverageVerdict(BaseModel):
    covered: bool
    matched_statement: Optional[str] = None
    reason: str


def _find_tof_excel() -> Path:
    for p in MATERIALS_ROOT.rglob("*.xlsx"):
        if "тоф" in p.name.lower():
            return p
    raise FileNotFoundError("Excel Примера 4 (ТОФ) не найден рядом с репозиторием")


async def compute_coverage(generated: list[Hypothesis], goldset: list[str]) -> dict:
    """Для каждой экспертной гипотезы спрашиваем LLM: есть ли среди
    сгенерированных хоть одна, покрывающая ту же техническую идею."""
    client = get_client()
    generated_text = "\n".join(f"- {h.statement}" for h in generated)
    details = []
    for expert_hyp in goldset:
        verdict = await client.acomplete_json(
            f"""Список сгенерированных системой гипотез:
{generated_text}

Экспертная гипотеза (реальная, из мозгового штурма на фабрике): {expert_hyp}

Есть ли среди сгенерированных гипотез хотя бы одна, покрывающая ту же
техническую идею, что и экспертная (не обязательно дословно)?""",
            CoverageVerdict,
            system_prompt="Ты сравниваешь списки исследовательских гипотез по обогащению руд на предмет покрытия идей.",
        )
        details.append({"expert_hypothesis": expert_hyp, **verdict.model_dump()})

    covered = sum(1 for d in details if d["covered"])
    return {"coverage_ratio": covered / len(goldset), "covered": covered, "total": len(goldset), "details": details}


async def compute_geval_scores(generated: list[Hypothesis]) -> list[dict]:
    judge = DeepEvalClientWrapper()
    metric = GEval(
        name="Конкретность и проверяемость",
        criteria=(
            "Гипотеза формулирует КОНКРЕТНОЕ техническое изменение оборудования, "
            "режима или реагента (а не общие слова вроде 'улучшить флотацию'), "
            "достаточно детальное, чтобы по нему можно было спланировать эксперимент. "
            "НЕ требуется, чтобы сама формулировка явно описывала методику "
            "испытаний (это отдельный шаг roadmap) — оценивай только конкретность "
            "самого технического изменения."
        ),
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=0.5,
    )
    results = []
    for h in generated:
        test_case = LLMTestCase(
            input=f"Механизм: {h.mechanism}\nОжидаемый эффект: {h.expected_effect}",
            actual_output=h.statement,
        )
        await metric.a_measure(test_case)
        results.append({"statement": h.statement, "geval_score": metric.score, "geval_reason": metric.reason})
    return results


async def main() -> None:
    goldset = json.loads(GOLDSET_PATH.read_text(encoding="utf-8"))["hypotheses"]
    excel_path = _find_tof_excel()
    print(f"[eval] LLM_PROVIDER={config.LLM_PROVIDER}, EMBEDDING_PROVIDER={config.EMBEDDING_PROVIDER}")
    print(f"[eval] Excel: {excel_path.name}")
    print(f"[eval] Goldset (held-out, {len(goldset)} гипотез экспертов ТОФ)")

    final: list[Hypothesis] = []
    async for event in run_pipeline(
        str(excel_path),
        goal="Снизить потери элементов 28 и 29 с хвостами",
        constraints="",
    ):
        if isinstance(event, list):
            final = event
        else:
            print(f"[eval] узел: {event.node}")

    print(f"\n[eval] Сгенерировано гипотез: {len(final)}")
    if not final:
        print("[eval] Пусто — дальше оценивать нечего (проверь LLM_PROVIDER/логи пайплайна).")
        return

    print("\n=== Coverage vs эксперты ===")
    coverage = await compute_coverage(final, goldset)
    print(f"Покрыто {coverage['covered']}/{coverage['total']} экспертных гипотез ({coverage['coverage_ratio'] * 100:.0f}%)")
    for d in coverage["details"]:
        mark = "[+]" if d["covered"] else "[ ]"
        print(f"  {mark} {d['expert_hypothesis']}")
        if d["covered"]:
            print(f"        -> {d['matched_statement']}")

    print("\n=== GEval: конкретность/проверяемость (deepeval) ===")
    geval_results = await compute_geval_scores(final)
    for r in geval_results:
        print(f"  [{r['geval_score']:.2f}] {r['statement'][:90]}")

    print("\n=== Ranker (уже посчитано пайплайном) ===")
    for h in final:
        print(
            f"  score={h.score} novelty={h.novelty} feasibility={h.feasibility} "
            f"impact={h.impact} risk={h.risk} | {h.statement[:60]}"
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm_provider": config.LLM_PROVIDER,
        "embedding_provider": config.EMBEDDING_PROVIDER,
        "excel_file": excel_path.name,
        "n_generated": len(final),
        "coverage": coverage,
        "geval": geval_results,
        "hypotheses": [h.model_dump(mode="json") for h in final],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[eval] Полный отчёт: {REPORT_PATH}")

    try:
        from render_report import render

        html_path = render(report, REPORT_PATH.with_suffix(".html"))
        print(f"[eval] HTML-отчёт: {html_path}")
    except Exception as e:  # noqa: BLE001 — рендер HTML не должен ронять сам eval
        print(f"[eval] Не удалось отрендерить HTML-отчёт: {e}")


if __name__ == "__main__":
    asyncio.run(main())
