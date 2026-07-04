"""Tails Analyzer: ParsedTails (из excel_tails.parse_loss_table) -> LossFinding[].

Детерминированная логика (НЕ LLM) — это главный дифференциатор решения: находки
привязаны к конкретным цифрам из отчёта, а не к общим словам из учебника
(см. PLAN.md §2). Проверено на реальных данных всех 4 примеров.
"""

from __future__ import annotations

import pandas as pd

from hypofactory.ingestion.excel_tails import COMBINED_MATERIAL_LABELS, GRANULAR_MATERIAL_LABELS, ParsedTails
from hypofactory.schemas import LossFinding

_ELEMENT_LABELS = {"element_28": "Элемент 28", "element_29": "Элемент 29"}

_FINE_CLASSES = {"-10", "-20+10"}
_COARSE_CLASSES = {"+125", "-125+71"}


def _interpret(size_class: str, mineral_form: str, recoverable: bool) -> str:
    form = mineral_form.lower()
    parts: list[str] = []

    if size_class in _FINE_CLASSES:
        parts.append("тонкий класс — вероятны шламообразование и переизмельчение")
    elif size_class in _COARSE_CLASSES:
        parts.append("крупный класс — вероятно недоизмельчение/недостаточное раскрытие сростков")

    if "закрытый" in form:
        parts.append(
            "минерал в закрытой (нераскрытой) форме — требуется более тонкое "
            "измельчение или доизмельчение в отдельном цикле"
        )
    elif "раскрытый" in form:
        parts.append(
            "минерал уже раскрыт, но теряется — потенциал в доводке схемы сепарации "
            "(доизмельчение, магнитная сепарация, дополнительная флотация)"
        )
    elif "силикат" in form or "валлериит" in form:
        parts.append("сростки с силикатами/валлериитом — трудноизвлекаемая форма, нужны спецреагенты")
    elif "пирротин" in form or "пирит" in form:
        parts.append("потери в сульфидной матрице пирротина/пирита — нужна более селективная флотация")
    elif "миллерит" in form:
        parts.append("миллерит — извлекаемый минерал, потеря может указывать на режим сепарации")

    if not recoverable:
        parts.append("текущей технологией практически не извлекается")

    return "; ".join(parts) if parts else "требует дополнительного анализа"


def analyze(parsed: ParsedTails, top_n_per_element: int = 5) -> list[LossFinding]:
    """top_n_per_element: сколько находок на элемент брать с каждой стороны
    (крупнейшие извлекаемые потери = точки роста; крупнейшие неизвлекаемые =
    контекст «это пока в принципе не взять»)."""
    df = parsed.mineral_rows
    if df.empty:
        return []

    # если в файле есть и гранулярная разбивка (породные/пирротиновые), и общий
    # комбинированный итог ("Хвосты отвальные") — общий итог дублирует гранулярные
    # суммы; берём только гранулярные, чтобы не задвоить тоннаж потерь
    materials_present = set(df["material"].unique())
    if materials_present & GRANULAR_MATERIAL_LABELS and materials_present & COMBINED_MATERIAL_LABELS:
        df = df[df["material"].isin(GRANULAR_MATERIAL_LABELS)]

    grouped = (
        df.groupby(["element", "size_class", "mineral_form", "recoverable"], as_index=False)["tons"]
        .sum()
        .dropna(subset=["tons"])
    )
    grouped = grouped[grouped["tons"] > 0]

    findings: list[LossFinding] = []
    for element in grouped["element"].unique():
        elem_df = grouped[grouped["element"] == element]
        total = elem_df["tons"].sum()
        if not total:
            continue

        top_recoverable = elem_df[elem_df["recoverable"]].sort_values("tons", ascending=False).head(
            top_n_per_element
        )
        top_non_recoverable = (
            elem_df[~elem_df["recoverable"]].sort_values("tons", ascending=False).head(top_n_per_element)
        )

        for _, row in pd.concat([top_recoverable, top_non_recoverable]).iterrows():
            share = float(row["tons"]) / total if total else None
            findings.append(
                LossFinding(
                    element=_ELEMENT_LABELS.get(element, element),
                    size_class=row["size_class"],
                    mineral_form=row["mineral_form"],
                    recoverable=bool(row["recoverable"]),
                    tons=round(float(row["tons"]), 2),
                    share_of_losses=round(share, 4) if share is not None else None,
                    interpretation=_interpret(row["size_class"], row["mineral_form"], bool(row["recoverable"])),
                )
            )

    findings.sort(key=lambda f: f.tons or 0, reverse=True)
    return findings
