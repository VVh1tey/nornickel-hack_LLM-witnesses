"""LossFinding[] + TargetSpec -> поисковые запросы для retrieve().

Шаблонно, без лишнего LLM-вызова: находки уже содержат конкретные термины
(элемент, класс крупности, минеральная форма, интерпретация) — этого достаточно
для релевантного поиска по корпусу без риска, что LLM исказит формулировку.
"""

from __future__ import annotations

from hypofactory.schemas import LossFinding, TargetSpec


def build_queries(findings: list[LossFinding], spec: TargetSpec, max_queries: int = 8) -> list[str]:
    queries: list[str] = [spec.goal]
    if spec.kpi:
        queries.append(spec.kpi)

    seen: set[tuple[str, str]] = set()
    for f in findings:
        key = (f.mineral_form, f.size_class)
        if key in seen:
            continue
        seen.add(key)
        queries.append(
            f"Как повысить извлечение {f.element} из класса крупности {f.size_class} мкм "
            f"в минеральной форме «{f.mineral_form}»? {f.interpretation}"
        )
        if len(queries) >= max_queries:
            break
    return queries[:max_queries]
