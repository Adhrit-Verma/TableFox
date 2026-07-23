from __future__ import annotations

import math

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
        context = node.metadata.get("context", {})
        context_text = " ".join(
            [
                str(context.get("description") or ""),
                str(context.get("owner") or ""),
                *(str(value) for item in context.get("documents", []) for value in item.values()),
            ]
        )
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
                context_text,
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
        if needle in context_text.lower():
            score += 10
            reasons.append("approved_context_match")
        if needle in str(context.get("owner") or "").lower():
            score += 5
            reasons.append("approved_owner_match")
        if node.kind in {"table", "view", "materialized_view"}:
            score += 20
            reasons.append("relation_kind_boost")
            row_estimate = int(node.metadata.get("row_estimate") or 0)
            if row_estimate > 0:
                score += min(5, int(math.log10(row_estimate + 1)))
                reasons.append("catalog_size_signal")
            usage = node.metadata.get("usage", {})
            if usage.get("status") == "available":
                scans = int(usage.get("sequential_scans") or 0) + int(
                    usage.get("index_scans") or 0
                )
                if scans:
                    score += min(10, int(math.log10(scans + 1) * 2))
                    reasons.append("aggregate_scan_activity")
                if usage.get("last_analyze"):
                    score += 1
                    reasons.append("analyze_freshness_signal")
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
                    "status": node.metadata.get("usage", {}).get("status", "unavailable"),
                    "source": "pg_stat_user_tables aggregates",
                    "raw_query_text_exposed": False,
                    "join_frequency": "unavailable",
                },
            },
        }
        for score, reasons, node in scored[:limit]
    ]
