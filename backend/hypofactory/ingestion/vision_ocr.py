"""Vision-разбор технологических схем флотации и регламентов/списков оборудования.

Каркас и доменные промпты — из черновика сокомандника (Норникель/ocr.py), но там
`_describe_image` был ЗАГЛУШКОЙ (возвращал захардкоженный текст) на локальном
Qwen2-VL-7B, который не влезает в наш Docker-образ. Здесь — реальный инференс
через yandex-ai-studio-sdk (sdk.chat.completions('gemma-3-27b-it') — единственная
модель AI Studio, понимающая картинки на момент написания, см. PLAN.md §3).

Поддерживает и standalone PNG/JPG (наши схемы флотации и регламенты — все PNG),
и PDF со встроенными изображениями (см. "схемы флот++.pdf").
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Optional

import fitz  # PyMuPDF

from hypofactory.llm.client import get_client
from hypofactory.schemas import DocumentChunk, EquipmentList

SCHEME_PROMPT = """Ты эксперт по обогащению полезных ископаемых.
Опиши данную технологическую схему или регламент.
Укажи основные аппараты (дробилки, мельницы, флотомашины, грохота, гидроциклоны),
потоки (концентрат, хвосты, промежуточные продукты, циркулирующие нагрузки)
и реагентный режим, если он указан.
Отвечай строго на русском языке, связным текстом."""

EQUIPMENT_PROMPT = """Ты эксперт по обогатительному оборудованию.
На изображении — список или схема оборудования обогатительной фабрики.
Перечисли КАЖДУЮ единицу оборудования отдельной строкой в формате:
Название | Тип | Параметры (производительность, размеры, если указаны)
Ничего, кроме этого списка, не пиши. Отвечай на русском языке."""


class DiagramVisionParser:
    def __init__(self, client: Optional[Any] = None) -> None:
        self.client = client or get_client()

    async def _describe(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        return await self.client.adescribe_image(image_bytes, prompt, mime_type=mime_type)

    async def parse_image(
        self, file_path: str, doc_type: str = "diagram_image", prompt: str = SCHEME_PROMPT
    ) -> DocumentChunk:
        """Standalone PNG/JPG — наши схемы флотации и регламенты."""
        image_bytes = Path(file_path).read_bytes()
        mime_type = mimetypes.guess_type(file_path)[0] or "image/png"
        description = await self._describe(image_bytes, mime_type, prompt)
        content = f"Технологическая схема/регламент ({Path(file_path).name}):\n{description}"
        return DocumentChunk(
            source_file=file_path,
            doc_type=doc_type,
            page_or_sheet=1,
            content=content,
            metadata={"source_type": "regulation", "contains_visual_data": True},
        )

    def _extract_images_from_pdf(self, file_path: str) -> list[tuple[int, bytes]]:
        doc = fitz.open(file_path)
        images = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            for img in page.get_images(full=True):
                xref = img[0]
                base_image = doc.extract_image(xref)
                images.append((page_num + 1, base_image["image"]))
        return images

    async def parse_pdf(self, file_path: str, prompt: str = SCHEME_PROMPT) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        for page_num, img_bytes in self._extract_images_from_pdf(file_path):
            description = await self._describe(img_bytes, "image/png", prompt)
            content = f"Технологическая схема на странице {page_num}:\n{description}"
            chunks.append(
                DocumentChunk(
                    source_file=file_path,
                    doc_type="diagram_image",
                    page_or_sheet=page_num,
                    content=content,
                    metadata={"source_type": "regulation", "contains_visual_data": True},
                )
            )
        return chunks

    async def parse(self, file_path: str) -> list[DocumentChunk]:
        suffix = Path(file_path).suffix.lower()
        if suffix == ".pdf":
            return await self.parse_pdf(file_path)
        return [await self.parse_image(file_path)]

    async def extract_equipment(self, file_path: str) -> EquipmentList:
        """Список/схема оборудования -> текстовое описание (vision) -> структурный
        JSON (обычная текстовая модель, у неё response_format надёжнее, чем у
        мультимодальной). Двухшаговая цепочка вместо одного vision+JSON вызова —
        так надёжнее при неопределённости, поддерживает ли SDK оба режима разом."""
        image_bytes = Path(file_path).read_bytes()
        mime_type = mimetypes.guess_type(file_path)[0] or "image/png"
        raw_list_text = await self._describe(image_bytes, mime_type, EQUIPMENT_PROMPT)
        return await self.client.acomplete_json(
            f"Преобразуй этот список оборудования в структурированный JSON:\n{raw_list_text}",
            EquipmentList,
            system_prompt="Ты извлекаешь структурированные данные об оборудовании обогатительной фабрики.",
        )
