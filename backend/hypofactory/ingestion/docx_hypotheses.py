"""Разбор DOCX с реальными гипотезами экспертов («мозговой штурм» по фабрикам).

Примеры 1-3 (КГМК, НОФ Вкр, НОФ мед) → hypotheses_db.json: база для few-shot
генератора и для verification («уже пробовали» — similarity к этим гипотезам).

Пример 4 (ТОФ) — ДЕРЖИМ HELD-OUT: идёт только в eval/goldsets/tof_expected.json
и нигде больше. Если его пустить в few-shot/corpus, метрика "сколько гипотез
экспертов система воспроизвела на скрытом примере" станет фиктивной (утечка).
"""

from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree

_NUMBERING_RE = re.compile(r"^\d+\.\s*")
_SKIP_PREFIXES = ("гипотезы по результатам",)

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def iter_paragraph_texts(path: str) -> list[str]:
    """Читаем сырой document.xml, а не python-docx.Document.paragraphs: в этих
    файлах нумерованный список оказался внутри структуры, которую python-docx
    не обходит (paragraphs отдаёт только прямых детей body — список остался
    пустым при проверке). XPath './/w:p' по всему дереву находит параграфы
    независимо от вложенности (content controls, и т.п.)."""
    with zipfile.ZipFile(path) as z:
        xml_bytes = z.read("word/document.xml")
    root = ElementTree.fromstring(xml_bytes)
    texts = []
    for p in root.iter(f"{_WORD_NS}p"):
        run_texts = [t.text for t in p.iter(f"{_WORD_NS}t") if t.text]
        paragraph_text = "".join(run_texts).strip()
        if paragraph_text:
            texts.append(paragraph_text)
    return texts


def extract_hypothesis_lines(path: str) -> list[str]:
    """Каждый пункт мозгового штурма — одна строка текста вида '1. <текст>'."""
    lines: list[str] = []
    for text in iter_paragraph_texts(path):
        if text.lower().startswith(_SKIP_PREFIXES):
            continue
        text = _NUMBERING_RE.sub("", text).strip()
        if text:
            lines.append(text)
    return lines


def build_hypotheses_db(factory_paths: dict[str, str]) -> dict[str, list[str]]:
    """factory_paths: {"КГМК": ".../Гипотезы КГМК.docx", ...} — ТОЛЬКО примеры 1-3."""
    return {factory: extract_hypothesis_lines(path) for factory, path in factory_paths.items()}


def build_goldset(path: str, factory: str = "ТОФ") -> dict:
    """Held-out эталон для eval (Пример 4). НЕ путать с build_hypotheses_db."""
    return {"factory": factory, "hypotheses": extract_hypothesis_lines(path)}
