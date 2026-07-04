"""Экспорт гипотез сессии в CSV/JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from hypofactory import config
from hypofactory.schemas import Hypothesis

EXPORTS_DIR = config.DATA_DIR / "sessions" / "exports"

_CSV_COLUMNS = [
    "id",
    "statement",
    "mechanism",
    "expected_effect",
    "novelty",
    "feasibility",
    "impact",
    "risk",
    "score",
    "status",
    "already_tried",
    "critic_verdict",
]


def export_json(session_id: str, hypotheses: list[Hypothesis]) -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORTS_DIR / f"{session_id}.json"
    data = [h.model_dump(mode="json") for h in hypotheses]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_csv(session_id: str, hypotheses: list[Hypothesis]) -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORTS_DIR / f"{session_id}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_COLUMNS)
        for h in hypotheses:
            writer.writerow(
                [
                    h.id,
                    h.statement,
                    h.mechanism,
                    h.expected_effect,
                    h.novelty,
                    h.feasibility,
                    h.impact,
                    h.risk,
                    h.score,
                    h.status.value,
                    h.already_tried or "",
                    h.critic_verdict or "",
                ]
            )
    return path
