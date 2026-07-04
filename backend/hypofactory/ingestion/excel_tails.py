"""Разбор отчётов «Хвосты <фабрика>.xlsx».

Две задачи в одном модуле:
1. `TailingsExcelParser.parse()` — линеаризация строк в текст для RAG-корпуса
   (черновик сокомандника, доработан: ключевые слова заголовка под реальные файлы).
2. `parse_loss_table()` — структурный разбор для Tails Analyzer: где именно и в какой
   минеральной форме теряется металл. Это НЕ табличный парсер по номерам строк —
   4 примера отличаются числом строк (119 vs 291 у ТОФ, где два блока: породные +
   пирротиновые хвосты), набором минеральных форм и даже написанием классов
   крупности ("+71" в Примере 1 == "-125 +71" в остальных). Парсер ищет
   структурные маркеры (заголовки таблиц) и читает данные до следующего "Итого".

Формат листа (обнаружен вручную на всех 4 примерах, см. PLAN.md §5.1):
    колонка B = метка, C = доля класса / (пусто для минеральных строк),
    D = доля Элемент 28 %, E = Элемент 28 т, F = доля Элемент 29 %, G = Элемент 29 т.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from hypofactory.schemas import DocumentChunk

# --- 1. Линеаризация для RAG (доработанный черновик сокомандника) ---------------

_HEADER_KEYWORDS = {
    "продукт", "выход", "содержание", "класс", "фракция",
    "элемент", "материал", "смт", "крупность", "cu", "au", "ag",
}


class TailingsExcelParser:
    def parse(self, file_path: str) -> list[DocumentChunk]:
        xl = pd.ExcelFile(file_path)
        chunks: list[DocumentChunk] = []

        for sheet_name in xl.sheet_names:
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)

            header_row_idx = self._find_header_row(df)
            if header_row_idx is None:
                continue

            df.columns = df.iloc[header_row_idx]
            df = df[header_row_idx + 1 :].dropna(how="all").reset_index(drop=True)

            for _, row in df.iterrows():
                if pd.isna(row.iloc[0]) or str(row.iloc[0]).lower() in ["итого", "всего", "сумма"]:
                    continue

                row_data = {str(k): v for k, v in row.items() if pd.notna(v) and str(k) != "nan"}
                description = self._linearize_row(row_data, sheet_name)

                chunks.append(
                    DocumentChunk(
                        source_file=file_path,
                        doc_type="tailings_excel",
                        page_or_sheet=sheet_name,
                        content=description,
                        metadata={
                            "source_type": "lab_report",
                            "table_name": sheet_name,
                            "raw_data_json": str(row_data),
                        },
                    )
                )
        return chunks

    def _find_header_row(self, df: pd.DataFrame) -> Optional[int]:
        for i in range(min(10, len(df))):
            row_text = " ".join([str(x).lower() for x in df.iloc[i] if pd.notna(x)])
            if any(k in row_text for k in _HEADER_KEYWORDS):
                return i
        return 0

    def _linearize_row(self, row_dict: dict, sheet_name: str) -> str:
        parts = [f"Данные по листу '{sheet_name}':"]
        for key, value in row_dict.items():
            parts.append(f"{key} составляет {value}.")
        return " ".join(parts)


# --- 2. Структурный разбор для Tails Analyzer -----------------------------------

RECOVERABLE_KEYWORDS_28 = ("pnt", "миллерит")
RECOVERABLE_KEYWORDS_29 = ("pnt",)  # для Эл.29 миллерит НЕ считается извлекаемым (см. "Как читать отчёт")

GRANULAR_MATERIAL_LABELS = {"хвосты породные", "хвосты пирротиновые"}
# "Хвосты отвальные" встречается отдельно от "Отвальные хвосты" (другой порядок слов) в
# файлах, где породные и пирротиновые хвосты разбиты по отдельности (напр. Пример 4/ТОФ):
# это ПОВТОРНАЯ, комбинированная (породные+пирротиновые) крупностная разбивка ОБЩЕГО
# объёма — если её не распознать как отдельный материал, её строки ошибочно припишутся
# последнему встреченному материалу (пирротиновые) и задвоят тоннаж потерь.
COMBINED_MATERIAL_LABELS = {"хвосты отвальные"}
_MATERIAL_LABELS = GRANULAR_MATERIAL_LABELS | COMBINED_MATERIAL_LABELS
_CLASS_SYNONYMS = {"+71": "-125+71"}  # наблюдаемая несогласованность между фабриками


def _normalize_size_class(raw: str) -> str:
    s = raw.lower().replace("мкм", "")
    s = re.sub(r"\s+", "", s)
    return _CLASS_SYNONYMS.get(s, s)


def _to_float(x) -> Optional[float]:
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def _is_recoverable(mineral_form: str, element: str) -> bool:
    form = mineral_form.lower()
    keywords = RECOVERABLE_KEYWORDS_28 if element.endswith("28") else RECOVERABLE_KEYWORDS_29
    return any(k in form for k in keywords)


@dataclass
class ParsedTails:
    """Результат структурного разбора одного файла «Хвосты».

    size_class_rows: доля/тонны по классам крупности целиком (без разбивки по минералам).
    mineral_rows: разбивка по минеральным формам ВНУТРИ каждого класса крупности —
        основной вход для Tails Analyzer.
    totals: общие тонны по (материал, элемент) — знаменатель для share_of_losses.
    """

    size_class_rows: pd.DataFrame
    mineral_rows: pd.DataFrame
    totals: dict[tuple[str, str], float] = field(default_factory=dict)


def parse_loss_table(file_path: str, sheet_name: Optional[str] = None) -> ParsedTails:
    df = pd.read_excel(file_path, sheet_name=sheet_name or 0, header=None)

    size_class_records: list[dict] = []
    mineral_records: list[dict] = []
    totals: dict[tuple[str, str], float] = {}

    current_material = "хвосты"
    n_rows = len(df)
    i = 0
    while i < n_rows:
        row = df.iloc[i]
        label = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""

        if label.lower() in _MATERIAL_LABELS:
            current_material = label.lower()
            i += 1
            continue

        if label.startswith("Класс крупности"):
            i += 1
            while i < n_rows:
                r = df.iloc[i]
                class_label = str(r.iloc[1]).strip() if pd.notna(r.iloc[1]) else ""
                if not class_label:
                    i += 1
                    continue
                if class_label.lower().startswith("итого"):
                    for elem in ("28", "29"):
                        tons = _to_float(r.iloc[4] if elem == "28" else r.iloc[6])
                        if tons is not None:
                            totals[(current_material, elem)] = tons
                    i += 1
                    break
                size_class_records.append(
                    {
                        "material": current_material,
                        "size_class": _normalize_size_class(class_label),
                        "share_of_class_pct": _to_float(r.iloc[2]),
                        "element_28_pct": _to_float(r.iloc[3]),
                        "element_28_tons": _to_float(r.iloc[4]),
                        "element_29_pct": _to_float(r.iloc[5]),
                        "element_29_tons": _to_float(r.iloc[6]),
                    }
                )
                i += 1
            continue

        col_d = str(row.iloc[3]).strip() if pd.notna(row.iloc[3]) else ""
        if col_d.startswith("Доля потерь Элемент 28") and label:
            size_class = _normalize_size_class(label)
            i += 1
            while i < n_rows:
                r = df.iloc[i]
                mineral_form = str(r.iloc[1]).strip() if pd.notna(r.iloc[1]) else ""
                if not mineral_form:
                    i += 1
                    continue
                if mineral_form.lower().startswith("итого"):
                    i += 1
                    break
                if mineral_form.lower() in ("извлекаемый металл", "не извлекаемый металл"):
                    i += 1
                    continue
                for elem, pct_col, tons_col in (("element_28", 3, 4), ("element_29", 5, 6)):
                    tons = _to_float(r.iloc[tons_col])
                    mineral_records.append(
                        {
                            "material": current_material,
                            "size_class": size_class,
                            "mineral_form": mineral_form,
                            "element": elem,
                            "share_within_class_pct": _to_float(r.iloc[pct_col]),
                            "tons": tons,
                            "recoverable": _is_recoverable(mineral_form, elem),
                        }
                    )
                i += 1
            continue

        i += 1

    return ParsedTails(
        size_class_rows=pd.DataFrame(size_class_records),
        mineral_rows=pd.DataFrame(mineral_records),
        totals=totals,
    )
