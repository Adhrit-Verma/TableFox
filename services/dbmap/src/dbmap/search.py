from __future__ import annotations

from .models import GraphNode, GraphSnapshot


def search_snapshot(snapshot: GraphSnapshot, query: str, limit: int = 25) -> list[dict]:
    needle = query.strip().lower()
    if not needle:
        return []

    kind_priority = {
        "table": 0,
        "view": 1,
        "materialized_view": 2,
        "column": 3,
        "constraint": 4,
        "index": 5,
        "schema": 6,
    }
    scored: list[tuple[int, GraphNode]] = []
    for node in snapshot.nodes:
        haystack = " ".join(
            str(value or "")
            for value in [
                node.id,
                node.kind,
                node.label,
                node.schema,
                node.name,
                node.metadata.get("comment"),
                node.metadata.get("data_type"),
                node.metadata.get("table"),
            ]
        ).lower()
        if needle not in haystack:
            continue
        name = (node.name or "").lower()
        label = node.label.lower()
        if name == needle:
            score = 100
        elif label == needle:
            score = 95
        elif name.startswith(needle):
            score = 60
        elif label.startswith(needle):
            score = 55
        else:
            score = 10
        if node.kind in {"table", "view", "materialized_view"}:
            score += 20
        scored.append((score, node))

    scored.sort(
        key=lambda item: (
            -item[0],
            kind_priority.get(item[1].kind, 99),
            item[1].label,
        )
    )
    return [
        {
            "id": node.id,
            "kind": node.kind,
            "label": node.label,
            "schema": node.schema,
            "parent_id": node.parent_id,
            "metadata": node.metadata,
            "score": score,
        }
        for score, node in scored[:limit]
    ]
