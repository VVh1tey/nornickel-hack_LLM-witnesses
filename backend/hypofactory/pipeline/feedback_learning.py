"""Обучение на фидбэке эксперта — approved-часть: одобренные гипотезы
пополняют пул для retrieval-based few-shot (см. generator.py._retrieve_fewshot),
а не статично дампятся в промпт — иначе он рос бы неограниченно с каждым
approve. При добавлении — дедуп (cosine-похожесть на уже существующее в
пуле), чтобы пул рос только по-настоящему новыми идеями, а не почти-копиями
одной и той же мысли (иначе top-K retrieval начал бы возвращать несколько
вариантов одного и того же вместо разнообразия).

Rejected-часть (фильтр по сходству с отклонённым) — отдельный модуль
rejected_index.py, использует ту же cached_embed_texts, но семантически
другая операция (исключение, а не отбор для промпта).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

from hypofactory import config
from hypofactory.llm.embeddings import aembed, cosine_similarity
from hypofactory.schemas import Hypothesis

APPROVED_POOL_PATH = config.PROCESSED_DIR / "approved_hypotheses.json"

# Порог заметно выше, чем ALREADY_TRIED_CANDIDATE_THRESHOLD (0.35) в
# verification.py: там ищем "может быть похоже, стоит спросить LLM", здесь —
# просто не плодим в пуле почти дословные копии одной и той же формулировки.
DEDUP_THRESHOLD = 0.85


def load_approved_pool() -> list[dict]:
    if APPROVED_POOL_PATH.exists():
        return json.loads(APPROVED_POOL_PATH.read_text(encoding="utf-8"))
    return []


def _save_approved_pool(pool: list[dict]) -> None:
    APPROVED_POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPROVED_POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


async def add_approved_hypothesis(hyp: Hypothesis, session_id: str) -> None:
    pool = load_approved_pool()
    if pool:
        pool_texts = [p["statement"] for p in pool]
        embeddings = await aembed(pool_texts + [hyp.statement], context="document")
        pool_emb, new_emb = embeddings[:-1], embeddings[-1:]
        sims = cosine_similarity(new_emb, pool_emb)[0]
        if sims.size and float(np.max(sims)) >= DEDUP_THRESHOLD:
            return  # уже есть похожая формулировка в пуле — не плодим копию

    pool.append(
        {
            "statement": hyp.statement,
            "mechanism": hyp.mechanism,
            "expected_effect": hyp.expected_effect,
            "session_id": session_id,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_approved_pool(pool)
