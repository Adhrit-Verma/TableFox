from __future__ import annotations

from .models import GraphSnapshot


def explain_object(snapshot: GraphSnapshot, node_id: str) -> dict:
    nodes = {node.id: node for node in snapshot.nodes}
    node = nodes.get(node_id)
    if not node:
        return {"found": False, "id": node_id}

    incoming = [edge for edge in snapshot.edges if edge.target == node_id]
    outgoing = [edge for edge in snapshot.edges if edge.source == node_id]
    columns = [
        nodes[edge.target]
        for edge in outgoing
        if edge.kind == "has_column" and edge.target in nodes
    ]
    foreign_targets = [
        nodes[edge.target]
        for edge in outgoing
        if edge.kind == "foreign_key" and edge.target in nodes
    ]
    referenced_by = [
        nodes[edge.source]
        for edge in incoming
        if edge.kind == "foreign_key" and edge.source in nodes
    ]

    return {
        "found": True,
        "id": node.id,
        "kind": node.kind,
        "label": node.label,
        "schema": node.schema,
        "metadata": node.metadata,
        "summary": _summary(node.kind, node.label, columns, foreign_targets, referenced_by),
        "columns": [
            {
                "id": column.id,
                "name": column.name,
                "data_type": column.metadata.get("data_type"),
                "is_nullable": column.metadata.get("is_nullable"),
                "comment": column.metadata.get("comment"),
            }
            for column in columns
        ],
        "foreign_keys_to": [{"id": item.id, "label": item.label} for item in foreign_targets],
        "referenced_by": [{"id": item.id, "label": item.label} for item in referenced_by],
    }


def _summary(kind: str, label: str, columns: list, foreign_targets: list, referenced_by: list) -> str:
    parts = [f"{label} is a {kind.replace('_', ' ')}."]
    if columns:
        parts.append(f"It has {len(columns)} columns.")
    if foreign_targets:
        parts.append("It references " + ", ".join(item.label for item in foreign_targets[:5]) + ".")
    if referenced_by:
        parts.append("It is referenced by " + ", ".join(item.label for item in referenced_by[:5]) + ".")
    return " ".join(parts)
