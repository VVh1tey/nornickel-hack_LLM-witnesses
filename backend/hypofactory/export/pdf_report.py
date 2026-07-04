"""Экспорт бизнес-отчёта PDF: то же содержание, что docx_report.py (цель, топ
гипотез с обоснованием, источниками и roadmap), другой формат.

fpdf2 не умеет кириллицу через встроенные core-шрифты (Helvetica и т.п.) —
нужен настоящий Unicode TTF. В Docker ставим fonts-dejavu-core (см.
Dockerfile) — так шрифт не тащим бинарником в git; для локальной разработки
(Windows/macOS) берём системный Arial, он тоже содержит кириллицу.
"""

from __future__ import annotations

import os
from pathlib import Path

from fpdf import FPDF

from hypofactory import config
from hypofactory.schemas import Hypothesis

EXPORTS_DIR = config.DATA_DIR / "sessions" / "exports"

_FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),  # Debian/Ubuntu (наш Docker-образ)
    Path("C:/Windows/Fonts/arial.ttf"),  # Windows (локальная разработка)
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),  # macOS
    Path("/Library/Fonts/Arial.ttf"),  # macOS (альтернативный путь установки)
]


def _find_unicode_font() -> Path:
    override = os.getenv("PDF_FONT_PATH")
    if override and Path(override).exists():
        return Path(override)
    for candidate in _FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "Не найден Unicode-шрифт для PDF-экспорта (нужна кириллица). "
        "Установи fonts-dejavu-core (apt-get, уже в Dockerfile) или укажи "
        "путь к TTF-файлу через PDF_FONT_PATH в .env."
    )


class _ReportPDF(FPDF):
    def __init__(self, font_path: Path) -> None:
        super().__init__()
        self.add_font("Body", "", str(font_path))
        self.add_font("Body", "B", str(font_path))  # нет отдельного bold-файла — жирность не рендерится, только акцент размером
        self.set_font("Body", size=11)
        self.set_auto_page_break(auto=True, margin=15)

    def heading(self, text: str, size: int = 14) -> None:
        self.set_font("Body", "B", size)
        self.multi_cell(0, 8, text)
        self.set_font("Body", "", 11)
        self.ln(1)

    def paragraph(self, text: str) -> None:
        self.multi_cell(0, 6, text)
        self.ln(1)


def export_pdf(session_id: str, goal: str, hypotheses: list[Hypothesis]) -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORTS_DIR / f"{session_id}.pdf"

    pdf = _ReportPDF(_find_unicode_font())
    pdf.add_page()
    pdf.heading("Фабрика гипотез — отчёт", size=18)
    pdf.paragraph(f"Цель: {goal}")
    pdf.paragraph(f"Сгенерировано гипотез: {len(hypotheses)}")

    for i, hyp in enumerate(hypotheses, start=1):
        pdf.heading(f"{i}. {hyp.statement}", size=13)
        pdf.paragraph(f"Механизм влияния: {hyp.mechanism}")
        pdf.paragraph(f"Ожидаемый эффект: {hyp.expected_effect}")

        scores = (
            f"Новизна: {hyp.novelty} | Реализуемость: {hyp.feasibility} | "
            f"Эффект: {hyp.impact} | Риск: {hyp.risk} | Итоговый скор: {hyp.score}"
        )
        pdf.paragraph(scores)

        if hyp.already_tried:
            pdf.paragraph(f"Уже пробовали: {hyp.already_tried}")
        if hyp.critic_verdict:
            pdf.paragraph(f"Вердикт критика: {hyp.critic_verdict}")

        if hyp.sources:
            pdf.paragraph("Источники:")
            for src in hyp.sources:
                quote = f" — «{src.quote}»" if src.quote else ""
                pdf.paragraph(f"  • {src.source}{quote}")

        if hyp.roadmap:
            pdf.paragraph("Дорожная карта проверки:")
            for step in hyp.roadmap:
                extra = []
                if step.resources:
                    extra.append(f"ресурсы: {step.resources}")
                if step.success_criteria:
                    extra.append(f"критерий успеха: {step.success_criteria}")
                suffix = f" ({'; '.join(extra)})" if extra else ""
                pdf.paragraph(f"  {step.step}{suffix}")

    pdf.output(str(path))
    return path
