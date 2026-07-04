"""Тесты структурного Excel-парсера (I3/I8) на всех 4 реальных примерах.

Пути к примерам ищем через rglob, а не хардкодим — часть имён файлов в
раздаточных материалах хранится в NFD-нормализации Unicode (см. PLAN.md §5.1,
build_corpus.py), надёжнее матчить по расширению + подстроке "хвосты" в имени.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypofactory.analysis.tails_analyzer import analyze
from hypofactory.ingestion.excel_tails import parse_loss_table

MATERIALS_ROOT = Path(__file__).resolve().parents[2] / "Задача 1. Фабрика гипотез" / "Задача 1"


def _find_tails_files() -> list[Path]:
    if not MATERIALS_ROOT.exists():
        return []
    return sorted(p for p in MATERIALS_ROOT.rglob("*.xlsx") if "хвосты" in p.name.lower())


TAILS_FILES = _find_tails_files()

pytestmark = pytest.mark.skipif(
    not TAILS_FILES, reason="раздаточные материалы хакатона не найдены рядом с репозиторием"
)


@pytest.mark.parametrize("path", TAILS_FILES, ids=[p.name for p in TAILS_FILES])
def test_parse_loss_table_smoke(path: Path) -> None:
    parsed = parse_loss_table(str(path))
    assert not parsed.mineral_rows.empty, f"{path.name}: ни одной строки минерального разбора не найдено"
    assert set(parsed.mineral_rows["element"].unique()) <= {"element_28", "element_29"}
    assert parsed.mineral_rows["tons"].dropna().ge(0).all(), "тонны не могут быть отрицательными"


@pytest.mark.parametrize("path", TAILS_FILES, ids=[p.name for p in TAILS_FILES])
def test_size_class_shares_sum_to_100(path: Path) -> None:
    parsed = parse_loss_table(str(path))
    if parsed.size_class_rows.empty:
        pytest.skip(f"{path.name}: таблица классов крупности не распознана")
    for material, group in parsed.size_class_rows.groupby("material"):
        total_share = group["share_of_class_pct"].sum()
        assert 95 <= total_share <= 105, (
            f"{path.name}/{material}: доли классов крупности суммируются в {total_share}, ожидалось ~100"
        )


def test_kgmk_known_totals() -> None:
    """Перекрёстная проверка с ручным разбором «Хвосты КГМК.xlsx» (строка «Итого
    (проверка)» отчёта): element_28 ~= 10392.33 т, element_29 ~= 4229.74 т."""
    kgmk_files = [p for p in TAILS_FILES if "кгмк" in p.name.lower()]
    if not kgmk_files:
        pytest.skip("Хвосты КГМК.xlsx не найден")

    parsed = parse_loss_table(str(kgmk_files[0]))
    totals_by_element = parsed.mineral_rows.groupby("element")["tons"].sum()
    assert totals_by_element["element_28"] == pytest.approx(10392.33, abs=1.0)
    assert totals_by_element["element_29"] == pytest.approx(4229.74, abs=1.0)


@pytest.mark.parametrize("path", TAILS_FILES, ids=[p.name for p in TAILS_FILES])
def test_analyze_produces_sorted_findings(path: Path) -> None:
    parsed = parse_loss_table(str(path))
    findings = analyze(parsed)
    assert findings, f"{path.name}: analyze() не вернул ни одной находки"

    tons_sequence = [f.tons for f in findings]
    assert tons_sequence == sorted(tons_sequence, reverse=True), "находки должны быть отсортированы по убыванию тонн"

    for finding in findings:
        assert finding.tons is not None and finding.tons > 0
        assert finding.interpretation
        assert finding.element in ("Элемент 28", "Элемент 29")
