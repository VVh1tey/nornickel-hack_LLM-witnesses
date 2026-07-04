"""История сессий — Postgres (docker-compose: сервис postgres), JSONB-блоб на
сессию (не нормализуем гипотезы в отдельные таблицы — для истории запросов
хакатона это лишняя сложность). Таблица создаётся сама при первом обращении."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal, Optional

import asyncpg

from hypofactory import config
from hypofactory.schemas import Hypothesis, PipelineStatus, RankingWeights

SessionStatusLiteral = Literal["running", "done", "error"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_created_at_idx ON sessions (created_at DESC);
"""

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=config.POSTGRES_DSN, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await conn.execute(_SCHEMA)
    return _pool


class SessionState:
    def __init__(
        self,
        session_id: str,
        goal: str,
        constraints: str = "",
        weights: Optional[RankingWeights] = None,
        name: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.goal = goal
        self.constraints = constraints
        self.weights = weights or RankingWeights()
        self.status: SessionStatusLiteral = "running"
        self.progress: list[PipelineStatus] = []
        self.hypotheses: list[Hypothesis] = []
        self.error: Optional[str] = None
        self.name: Optional[str] = name  # человекочитаемое имя сессии, задаётся/меняется пользователем

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "created_at": self.created_at,
            "goal": self.goal,
            "constraints": self.constraints,
            "weights": self.weights.model_dump(),
            "status": self.status,
            "progress": [p.model_dump() for p in self.progress],
            "hypotheses": [h.model_dump(mode="json") for h in self.hypotheses],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        session = cls(
            d["session_id"],
            d["goal"],
            d.get("constraints", ""),
            RankingWeights(**d["weights"]) if d.get("weights") else RankingWeights(),
            d.get("name"),
        )
        session.created_at = d["created_at"]
        session.status = d["status"]
        session.progress = [PipelineStatus(**p) for p in d.get("progress", [])]
        session.hypotheses = [Hypothesis(**h) for h in d.get("hypotheses", [])]
        session.error = d.get("error")
        return session


async def save_session(session: SessionState) -> None:
    pool = await get_pool()
    data = json.dumps(session.to_dict(), ensure_ascii=False)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (session_id, created_at, goal, status, data)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (session_id) DO UPDATE
            SET status = EXCLUDED.status, data = EXCLUDED.data
            """,
            session.session_id,
            datetime.fromisoformat(session.created_at),
            session.goal,
            session.status,
            data,
        )


async def load_session(session_id: str) -> Optional[SessionState]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT data FROM sessions WHERE session_id = $1", session_id)
    if row is None:
        return None
    return SessionState.from_dict(json.loads(row["data"]))


async def list_sessions(limit: int = 20, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT session_id, created_at, goal, status, data FROM sessions "
            "ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
    result = []
    for row in rows:
        data = json.loads(row["data"])
        result.append(
            {
                "session_id": row["session_id"],
                "name": data.get("name"),
                "created_at": row["created_at"].isoformat(),
                "goal": row["goal"],
                "constraints": data.get("constraints", ""),
                "weights": data.get("weights"),
                "status": row["status"],
                "n_hypotheses": len(data.get("hypotheses", [])),
            }
        )
    return result
