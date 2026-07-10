from __future__ import annotations

from .models import GraphNode, GraphSnapshot


def search_snapshot(snapshot: GraphSnapshot, query: str, limit: int = 25) -> list[dict]:
    needle = query.strip().lower()
    if not needle:
        return []

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
        score = 10 if node.label.lower() == needle else 5 if node.label.lower().startswith(needle) else 1
        scored.append((score, node))

    scored.sort(key=lambda item: (-item[0], item[1].kind, item[1].label))
    return [
        {
            "id": node.id,
            "kind": node.kind,
            "label": node.label,
            "schema": node.schema,
            "metadata": node.metadata,
            "score": score,
        }
        for score, node in scored[:limit]
    ]
