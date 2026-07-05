"""Визуализация связей: RetrievalResult (сущности + рёбра) -> самодостаточный HTML.

Закрывает требование ТЗ «визуальное представление связей (графы)» без полноценного
графа знаний в БД — рендерим подграф из того, что уже вернул LightRAG для
конкретной гипотезы (см. PLAN.md §1, замена Memgraph на pyvis-визуализацию).
cdn_resources="in_line" — html самодостаточен (без сети/файлов), это важно для
показа внутри Docker/iframe на демо.
"""

from __future__ import annotations

import re

import networkx as nx
from pyvis.network import Network

from hypofactory import config
from hypofactory.schemas import RetrievalResult

_RELATION_RE = re.compile(r"^(.*?) -\[(.*?)\]-> (.*)$")

GRAPHML_PATH = config.LIGHTRAG_DIR / "graph_chunk_entity_relation.graphml"

_FREEZE_PHYSICS_JS = """
<script>
  network.once("stabilizationIterationsDone", function () {
    network.setOptions({ physics: false });
  });
</script>
"""


def _freeze_physics_after_stabilization(html: str) -> str:
    """pyvis по умолчанию гоняет силовую симуляцию бесконечно — на графе с
    несвязными подкомпонентами (а у нас их много, сущности из разных чанков
    не всегда пересекаются) часть узлов никогда не успокаивается и трясётся
    вечно. Даём симуляции один раз стабилизироваться и дальше замораживаем."""
    return html.replace("</body>", _FREEZE_PHYSICS_JS + "</body>")


def graph_stats() -> dict:
    """Быстрая проверка «что вообще есть в графе» без дашборда: количество
    узлов/рёбер и несколько примеров. Читает GraphML напрямую (файловый
    граф-стор LightRAG — Qdrant заменяет только векторный индекс, см.
    lightrag_setup.py)."""
    if not GRAPHML_PATH.exists():
        return {"exists": False, "nodes": 0, "edges": 0, "sample_nodes": [], "sample_edges": []}
    g = nx.read_graphml(GRAPHML_PATH)
    return {
        "exists": True,
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "sample_nodes": list(g.nodes)[:20],
        "sample_edges": [f"{u} -> {v}" for u, v in list(g.edges)[:20]],
    }


def render_full_graph_html(height: str = "800px", max_nodes: int = 300) -> str:
    """Весь граф знаний целиком (не подграф одной гипотезы) — «дашборд»
    для LightRAG, которого у него самого в этой интеграции нет."""
    net = Network(height=height, width="100%", directed=True, cdn_resources="in_line")

    if not GRAPHML_PATH.exists():
        net.add_node("Нет данных", label="Граф ещё не построен — запусти index_lightrag.py")
        return net.generate_html()

    g = nx.read_graphml(GRAPHML_PATH)
    if g.number_of_nodes() > max_nodes:
        # без обрезки pyvis на большом графе тормозит/виснет в браузере
        top_nodes = sorted(g.degree, key=lambda x: x[1], reverse=True)[:max_nodes]
        g = g.subgraph([n for n, _ in top_nodes])

    for node, attrs in g.nodes(data=True):
        net.add_node(node, label=str(node), title=str(attrs.get("description", "")))
    for u, v, attrs in g.edges(data=True):
        net.add_edge(u, v, label=str(attrs.get("keywords", "")))

    return _freeze_physics_after_stabilization(net.generate_html())


def render_graph_html(result: RetrievalResult, height: str = "500px") -> str:
    net = Network(height=height, width="100%", directed=True, cdn_resources="in_line")
    added: set[str] = set()

    def ensure_node(name: str) -> None:
        if name not in added:
            net.add_node(name, label=name)
            added.add(name)

    for entity in result.entities:
        ensure_node(entity)

    for relation in result.relations:
        match = _RELATION_RE.match(relation)
        if not match:
            continue
        src, label, tgt = match.groups()
        ensure_node(src)
        ensure_node(tgt)
        net.add_edge(src, tgt, label=label)

    if not added:
        net.add_node("Нет данных", label="Граф пуст — нет извлечённых сущностей/связей")

    return _freeze_physics_after_stabilization(net.generate_html())
