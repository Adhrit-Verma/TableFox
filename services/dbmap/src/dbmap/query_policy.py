from __future__ import annotations

from collections import Counter
import json
import re
from typing import Any


MAX_PLAN_NODES = 1000
MAX_PLAN_BYTES = 250_000

_SENSITIVE_MARKERS = {
    "credential": {
        "api_key",
        "access_token",
        "password",
        "password_hash",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "token",
    },
    "personal_data": {
        "address",
        "date_of_birth",
        "dob",
        "email",
        "email_address",
        "mobile",
        "phone",
        "phone_number",
        "social_security_number",
        "ssn",
    },
}


def classify_sensitive_columns(column_names: list[str]) -> list[dict[str, str]]:
    """Return conservative, name-based classifications before rows leave the service."""
    findings: list[dict[str, str]] = []
    for column_name in column_names:
        normalized = re.sub(r"[^a-z0-9]+", "_", column_name.lower()).strip("_")
        for category, markers in _SENSITIVE_MARKERS.items():
            if any(_contains_marker(normalized, marker) for marker in markers):
                findings.append(
                    {
                        "column": column_name,
                        "category": category,
                        "detection": "column_name_heuristic",
                    }
                )
                break
    return findings


def assess_query_plan(
    payload: Any,
    *,
    max_total_cost: float,
    max_plan_rows: int,
    include_plan: bool = False,
) -> dict[str, Any]:
    document = _plan_document(payload)
    root = document.get("Plan")
    if not isinstance(root, dict):
        raise ValueError("PostgreSQL returned an invalid EXPLAIN plan.")

    node_types: Counter[str] = Counter()
    relations: set[str] = set()
    sequential_scans: set[str] = set()
    stack = [root]
    visited = 0
    truncated = False

    while stack:
        node = stack.pop()
        visited += 1
        if visited > MAX_PLAN_NODES:
            truncated = True
            break
        node_type = str(node.get("Node Type") or "Unknown")
        node_types[node_type] += 1
        relation = _relation_label(node)
        if relation:
            relations.add(relation)
            if node_type.endswith("Seq Scan"):
                sequential_scans.add(relation)
        children = node.get("Plans", [])
        if isinstance(children, list):
            stack.extend(child for child in children if isinstance(child, dict))

    total_cost = float(root.get("Total Cost") or 0)
    plan_rows = int(root.get("Plan Rows") or 0)
    blocking_reasons: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if total_cost > max_total_cost:
        blocking_reasons.append(
            {
                "code": "total_cost_exceeded",
                "value": total_cost,
                "limit": max_total_cost,
            }
        )
    if plan_rows > max_plan_rows:
        blocking_reasons.append(
            {
                "code": "estimated_rows_exceeded",
                "value": plan_rows,
                "limit": max_plan_rows,
            }
        )
    if sequential_scans:
        warnings.append(
            {
                "code": "sequential_scan",
                "relations": sorted(sequential_scans),
                "note": "Sequential scans can be valid for small tables; review in context.",
            }
        )
    if truncated:
        warnings.append(
            {
                "code": "plan_summary_truncated",
                "limit": MAX_PLAN_NODES,
            }
        )

    result: dict[str, Any] = {
        "executed": False,
        "within_policy": not blocking_reasons,
        "approval_required": bool(blocking_reasons),
        "summary": {
            "root_node_type": str(root.get("Node Type") or "Unknown"),
            "total_cost": total_cost,
            "estimated_rows": plan_rows,
            "plan_nodes": min(visited, MAX_PLAN_NODES),
            "node_types": dict(sorted(node_types.items())),
            "relations": sorted(relations),
            "sequential_scans": sorted(sequential_scans),
        },
        "policy": {
            "max_total_cost": max_total_cost,
            "max_estimated_rows": max_plan_rows,
            "statement_executed": False,
            "explain_analyze": False,
        },
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }
    if include_plan:
        encoded = json.dumps(payload, default=str)
        if len(encoded.encode("utf-8")) <= MAX_PLAN_BYTES:
            result["plan"] = payload
        else:
            result["plan_omitted"] = f"Plan exceeded {MAX_PLAN_BYTES} bytes."
    return result


def _contains_marker(value: str, marker: str) -> bool:
    return value == marker or value.startswith(f"{marker}_") or value.endswith(f"_{marker}") or f"_{marker}_" in value


def _plan_document(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    if isinstance(payload, dict):
        return payload
    raise ValueError("PostgreSQL returned an invalid EXPLAIN response.")


def _relation_label(node: dict[str, Any]) -> str | None:
    relation = node.get("Relation Name")
    if not relation:
        return None
    schema = node.get("Schema")
    return f"{schema}.{relation}" if schema else str(relation)
