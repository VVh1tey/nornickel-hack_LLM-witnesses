"""Реестр анализаторов входных данных по доменам (см. domain_profile.py).

Новый домен = новый модуль с функцией analyze_file(path) -> list[Finding-like]
+ запись здесь + analyzer=<ключ> в профиле домена — без правок графа
(pipeline/graph.py) и остального пайплайна (generator/verification/ranker/
roadmap уже работают с общими схемами, домен не различают)."""

from __future__ import annotations

from typing import Callable

from hypofactory.analysis.tails_analyzer import analyze as _analyze_tails
from hypofactory.ingestion.excel_tails import parse_loss_table
from hypofactory.schemas import LossFinding


def _analyze_tails_file(file_path: str) -> list[LossFinding]:
    return _analyze_tails(parse_loss_table(file_path))


ANALYZERS: dict[str, Callable[[str], list[LossFinding]]] = {
    "tails": _analyze_tails_file,
}


def get_analyzer(key: str) -> Callable[[str], list[LossFinding]]:
    if key not in ANALYZERS:
        raise ValueError(
            f"Неизвестный анализатор: {key!r}. Доступные: {list(ANALYZERS)}. "
            "Новый домен — новый модуль-анализатор, зарегистрировать здесь "
            "и указать analyzer=<ключ> в профиле (domain_profile.py)."
        )
    return ANALYZERS[key]
