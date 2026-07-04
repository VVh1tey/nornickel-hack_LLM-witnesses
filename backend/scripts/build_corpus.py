"""Оффлайн-сборка корпуса: все источники -> data/processed/{corpus.jsonl,
hypotheses_db.json, equipment.json} + backend/eval/goldsets/tof_expected.json.

Роутинг — по РАСПОЛОЖЕНИЮ (имя папки), а не по подстроке в имени файла: часть
файлов в раздаточных материалах хранит имена в NFD-нормализации Unicode
("й" = "и" + комбинирующая бревис, похоже на macOS-архив) — прямое сравнение
строк с литералами вроде "Типичный" при сравнении с NFC-литералами не совпадает.
Поэтому все сравнения строк здесь идут через unicodedata.normalize("NFC", ...),
а расположение файла — из имени родительской папки, где комбинирующих
символов нет ("Регламенты", "Схемы флотации", "Пример N").

Пример 4 (ТОФ) — held-out: его "Гипотезы ТОФ.docx" уходит ТОЛЬКО в
eval/goldsets/tof_expected.json, не в hypotheses_db.json и не в корпус.
Запуск: uv run python backend/scripts/build_corpus.py [путь_к_папке_с_материалами]
"""

from __future__ import annotations

import asyncio
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hypofactory import config
from hypofactory.ingestion.docx_hypotheses import build_goldset, extract_hypothesis_lines
from hypofactory.ingestion.excel_tails import TailingsExcelParser
from hypofactory.ingestion.pdf_books import TextbookPDFParser
from hypofactory.ingestion.vision_ocr import DiagramVisionParser
from hypofactory.schemas import DocumentChunk


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _has_real_content(d: Path) -> bool:
    return any(p.name != ".gitkeep" for p in d.iterdir())


def _find_input_dir(cli_arg: str | None) -> Path:
    if cli_arg:
        return Path(cli_arg)
    # раздаточные материалы хакатона лежат в корне репо — приоритет им;
    # data/raw/ пока содержит только .gitkeep-плейсхолдер (см. .gitignore)
    candidate = config.ROOT / "Задача 1. Фабрика гипотез" / "Задача 1"
    if candidate.exists():
        return candidate
    if config.RAW_DIR.exists() and _has_real_content(config.RAW_DIR):
        return config.RAW_DIR
    raise SystemExit(
        f"Не нашёл материалы ни в {candidate}, ни в {config.RAW_DIR}. "
        "Передай путь первым аргументом."
    )


async def build(input_dir: Path) -> None:
    pdf_parser = TextbookPDFParser()
    excel_parser = TailingsExcelParser()
    vision_parser = DiagramVisionParser()

    corpus: list[DocumentChunk] = []
    hypotheses_paths: dict[str, str] = {}
    goldset_path: Path | None = None
    equipment_lists = []

    for path in input_dir.rglob("*"):
        if not path.is_file():
            continue
        parent = _nfc(path.parent.name)
        name = _nfc(path.name)
        suffix = path.suffix.lower()

        try:
            if suffix == ".pdf" and parent == "Дополнительные материалы":
                book_chunks = pdf_parser.parse(str(path))
                if not book_chunks:
                    print(
                        f"[build_corpus] ВНИМАНИЕ: 0 чанков из {path.name} — похоже, это "
                        "скан без текстового слоя (PyMuPDF не извлёк текст). Нужен OCR "
                        "постранично (vision_ocr.py умеет, но 455 страниц — дорого по API; "
                        "решить отдельно, есть ли на это время)."
                    )
                corpus.extend(book_chunks)

            elif suffix == ".pdf" and parent == "Схемы флотации":
                corpus.extend(await vision_parser.parse_pdf(str(path)))

            elif suffix in (".png", ".jpg", ".jpeg") and parent == "Схемы флотации":
                corpus.append(await vision_parser.parse_image(str(path)))

            elif suffix in (".png", ".jpg", ".jpeg") and parent == "Регламенты":
                corpus.append(await vision_parser.parse_image(str(path), doc_type="regulation_pdf"))
                if "оборудован" in name.lower():
                    equipment_lists.append(await vision_parser.extract_equipment(str(path)))

            elif suffix == ".xlsx" and "хвосты" in name.lower():
                corpus.extend(excel_parser.parse(str(path)))

            elif suffix == ".docx" and "гипотезы" in name.lower():
                if parent == "Пример 4":
                    goldset_path = path
                else:
                    factory = name.replace("Гипотезы", "").replace(".docx", "").strip()
                    hypotheses_paths[factory] = str(path)

            elif suffix == ".docx" and "хвост" in name.lower():
                # "Как читать отчёт института по хвостам.docx" — методология, в корпус
                from hypofactory.ingestion.docx_hypotheses import iter_paragraph_texts

                text = "\n".join(iter_paragraph_texts(str(path)))
                corpus.append(
                    DocumentChunk(
                        source_file=str(path),
                        doc_type="textbook_pdf",
                        page_or_sheet=1,
                        content=text,
                        metadata={"source_type": "methodology"},
                    )
                )
        except Exception as e:  # noqa: BLE001 — не роняем всю сборку из-за одного файла
            print(f"[build_corpus] ОШИБКА при обработке {path}: {e}")

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.CORPUS_PATH, "w", encoding="utf-8") as f:
        for chunk in corpus:
            f.write(chunk.model_dump_json() + "\n")
    print(f"[build_corpus] corpus.jsonl: {len(corpus)} чанков -> {config.CORPUS_PATH}")

    hypotheses_db = {
        factory: extract_hypothesis_lines(p) for factory, p in hypotheses_paths.items()
    }
    config.HYPOTHESES_DB_PATH.write_text(
        json.dumps(hypotheses_db, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[build_corpus] hypotheses_db.json: {list(hypotheses_db.keys())} -> {config.HYPOTHESES_DB_PATH}")

    merged_equipment = [item.model_dump() for eq in equipment_lists for item in eq.items]
    config.EQUIPMENT_PATH.write_text(
        json.dumps({"items": merged_equipment}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[build_corpus] equipment.json: {len(merged_equipment)} позиций -> {config.EQUIPMENT_PATH}")

    if goldset_path is not None:
        goldset = build_goldset(str(goldset_path))
        goldset_out = config.ROOT / "backend" / "eval" / "goldsets" / "tof_expected.json"
        goldset_out.parent.mkdir(parents=True, exist_ok=True)
        goldset_out.write_text(json.dumps(goldset, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[build_corpus] held-out goldset ({len(goldset['hypotheses'])} гипотез) -> {goldset_out}")
    else:
        print("[build_corpus] ВНИМАНИЕ: 'Гипотезы ТОФ.docx' (Пример 4) не найден — goldset не создан")


if __name__ == "__main__":
    input_dir = _find_input_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"[build_corpus] источник: {input_dir}")
    asyncio.run(build(input_dir))
