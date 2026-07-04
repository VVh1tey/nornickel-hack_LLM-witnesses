"""Обучение на фидбэке эксперта — rejected-часть: фильтрация свежесгенерированных
гипотез по сходству с РАНЕЕ ОТКЛОНЁННЫМИ, отдельным шагом в пайплайне сразу
после generator (не через рост промпта — см. feedback_learning.py для
симметричной approved-части).

Отклонённые гипотезы копятся в отдельный кэшированный эмбеддинг-индекс (тот
же cached_embed_texts, что и approved-пул) — при проверке пересчитываются
только НОВЫЕ записи с прошлого раза, весь пул заново не эмбеддится. Кэш и так
обновляется лениво на каждой проверке; rebuild_rejected_index() — форсированная
пересборка по кнопке (см. api/app.py), нужна редко (например при смене модели
эмбеддингов), не для регулярного использования.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel

from hypofactory import config
from hypofactory.llm.client import get_client
from hypofactory.llm.embeddings import aembed, cached_embed_texts, cosine_similarity
from hypofactory.schemas import Hypothesis

REJECTED_POOL_PATH = config.PROCESSED_DIR / "rejected_hypotheses.json"
REJECTED_CACHE_PATH = config.PROCESSED_DIR / "rejected_embeddings_cache.npz"

# Ниже этого даже не спрашиваем LLM — темы точно разные (тот же приём и порог,
# что ALREADY_TRIED_CANDIDATE_THRESHOLD в verification.py).
CANDIDATE_THRESHOLD = 0.35


def load_rejected_pool() -> list[dict]:
    if REJECTED_POOL_PATH.exists():
        return json.loads(REJECTED_POOL_PATH.read_text(encoding="utf-8"))
    return []


def _save_rejected_pool(pool: list[dict]) -> None:
    REJECTED_POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    REJECTED_POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


async def add_rejected_hypothesis(hyp: Hypothesis, session_id: str) -> None:
    pool = load_rejected_pool()
    pool.append(
        {
            "statement": hyp.statement,
            "mechanism": hyp.mechanism,
            "comment": hyp.comment,
            "session_id": session_id,
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_rejected_pool(pool)


async def rebuild_rejected_index() -> dict:
    """Форсированная пересборка кэша эмбеддингов (см. docstring модуля —
    обычно не нужна явно, кэш и так актуален лениво)."""
    pool = load_rejected_pool()
    texts = [p["statement"] for p in pool]
    embeddings = await cached_embed_texts(texts, REJECTED_CACHE_PATH)
    return {"n_rejected": len(pool), "n_embedded": int(embeddings.shape[0])}


class RejectSimilarityVerdict(BaseModel):
    same_idea: bool
    reason: str


async def filter_against_rejected(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    """Убирает из списка гипотезы, совпадающие по сути с ранее отклонёнными
    экспертом (эмбеддинг-кандидат + LLM-подтверждение — тот же гибридный
    паттерн, что check_already_tried в verification.py, но раздельно: одно
    дело "уже пробовали/похоже на историческую", другое — "эксперт явно
    забраковал похожую идею на этой самой фабрике")."""
    pool = load_rejected_pool()
    if not pool or not hypotheses:
        return hypotheses

    pool_texts = [p["statement"] for p in pool]
    pool_embeddings = await cached_embed_texts(pool_texts, REJECTED_CACHE_PATH)

    hyp_embeddings = await aembed([h.statement for h in hypotheses], context="query")
    sims = cosine_similarity(hyp_embeddings, pool_embeddings)  # (n_hyp, n_pool)

    client = get_client()
    kept: list[Hypothesis] = []
    for i, hyp in enumerate(hypotheses):
        best_idx = int(sims[i].argmax())
        if sims[i, best_idx] < CANDIDATE_THRESHOLD:
            kept.append(hyp)
            continue

        rejected_entry = pool[best_idx]
        verdict = await client.acomplete_json(
            f"""Гипотеза-кандидат: {hyp.statement}
Ранее отклонённая экспертом гипотеза: {rejected_entry['statement']}
Комментарий эксперта при отклонении: {rejected_entry.get('comment') or 'не указан'}

Это по сути одна и та же идея (даже если кандидат добавляет детали, которых
нет в отклонённой формулировке), или это разные идеи?""",
            RejectSimilarityVerdict,
            system_prompt=(
                "Ты сравниваешь исследовательские гипотезы обогащения руд на предмет "
                "совпадения сути идеи с ранее отклонённой экспертом гипотезой. "
                "Отвечай только на русском языке."
            ),
        )
        if not verdict.same_idea:
            kept.append(hyp)
        # same_idea=True -> гипотеза отбрасывается, не попадает в kept

    return kept
