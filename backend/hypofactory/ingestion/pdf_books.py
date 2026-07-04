"""PDF-парсер учебников (перенесено из черновика сокомандника, Норникель/pdf_parser.py).

Чистка колонтитулов (по первым 5 страницам) + чанкер по предложениям с overlap.
LightRAG всё равно ре-чанкует внутри себя по токенам — здесь важно не столько
точное совпадение с CHUNK_TOKEN_SIZE, сколько убрать типографский мусор
(колонтитулы, повторяющиеся заголовки) ДО индексации: чистый текст на входе —
главный рычаг качества извлечения сущностей (см. PLAN.md §3).
"""

from __future__ import annotations

import re

import fitz  # PyMuPDF

from hypofactory.schemas import DocumentChunk


class TextbookPDFParser:
    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 100):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"(\.|\?|\!)\s", r"\1\n", text)
        return text.strip()

    def _semantic_chunk(self, text: str) -> list[str]:
        sentences = text.split("\n")
        chunks = []
        current_chunk: list[str] = []
        current_len = 0

        for sentence in sentences:
            sent_len = len(sentence)
            if current_len + sent_len > self.chunk_size and current_chunk:
                chunks.append(" ".join(current_chunk))
                overlap_text = " ".join(current_chunk)[-self.chunk_overlap :]
                current_chunk = [overlap_text, sentence] if overlap_text else [sentence]
                current_len = len(overlap_text) + sent_len
            else:
                current_chunk.append(sentence)
                current_len += sent_len

        if current_chunk:
            chunks.append(" ".join(current_chunk))
        return chunks

    def parse(self, file_path: str) -> list[DocumentChunk]:
        doc = fitz.open(file_path)
        chunks: list[DocumentChunk] = []

        headers_footers: set[str] = set()
        for page_num in range(min(5, len(doc))):
            text = doc[page_num].get_text()
            lines = text.split("\n")
            if lines:
                headers_footers.add(lines[0].strip())
            if len(lines) > 1:
                headers_footers.add(lines[-1].strip())

        for page_num in range(len(doc)):
            page = doc[page_num]
            raw_text = page.get_text("text")

            for hf in headers_footers:
                if hf:
                    raw_text = raw_text.replace(hf, "")

            clean_text = self._clean_text(raw_text)
            if not clean_text:
                continue

            page_chunks = self._semantic_chunk(clean_text)
            for text_chunk in page_chunks:
                if len(text_chunk.strip()) < 20:
                    continue  # обрывки в 1-2 слова после чистки колонтитулов — шум, не знание
                chunks.append(
                    DocumentChunk(
                        source_file=file_path,
                        doc_type="textbook_pdf",
                        page_or_sheet=page_num + 1,
                        content=text_chunk,
                        metadata={"source_type": "textbook", "domain": "mineral_processing"},
                    )
                )
        return chunks
