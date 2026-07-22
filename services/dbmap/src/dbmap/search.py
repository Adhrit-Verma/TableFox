from __future__ import annotations

from .models import GraphNode, GraphSnapshot


def search_snapshot(snapshot: GraphSnapshot, query: str, limit: int = 25) -> list[dict]:
    needle = query.strip().lower()
    if not needle:
        return []
    limit = max(1, min(limit, 100))

    kind_priority = {
        "table": 0,
        "view": 1,
        "materialized_view": 2,
        "column": 3,
        "constraint": 4,
        "index": 5,
        "schema": 6,
    }
    scored: list[tuple[int, list[str], GraphNode]] = []
    for node in snapshot.nodes:
        comment = str(node.metadata.get("comment") or "")
        haystack = " ".join(
            str(value or "")
            for value in [
                node.id,
                node.kind,
                node.label,
                node.schema,
                node.name,
                comment,
                node.metadata.get("data_type"),
                node.metadata.get("table"),
            ]
        ).lower()
        if needle not in haystack:
            continue
        name = (node.name or "").lower()
        label = node.label.lower()
        reasons: list[str] = []
        if name == needle:
            score = 100
            reasons.append("exact_name")
        elif label == needle:
            score = 95
            reasons.append("exact_label")
        elif name.startswith(needle):
            score = 60
            reasons.append("name_prefix")
        elif label.startswith(needle):
            score = 55
            reasons.append("label_prefix")
        else:
            score = 10
            reasons.append("metadata_match")
        if needle in comment.lower():
            score += 5
            reasons.append("database_comment_match")
        if node.kind in {"table", "view", "materialized_view"}:
            score += 20
            reasons.append("relation_kind_boost")
        scored.append((score, reasons, node))

    scored.sort(
        key=lambda item: (
            -item[0],
            kind_priority.get(item[2].kind, 99),
            item[2].label,
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
            "ranking": {
                "score": score,
                "reasons": reasons,
                "usage_telemetry": {
                    "status": "unavailable",
                    "reason": "No approved aggregate usage source is configured.",
                },
            },
        }
        for score, reasons, node in scored[:limit]
    ]
