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
    constraints = [
        nodes[edge.target]
        for edge in outgoing
        if edge.kind == "has_constraint" and edge.target in nodes
    ]
    indexes = [
        nodes[edge.target]
        for edge in outgoing
        if edge.kind == "has_index" and edge.target in nodes
    ]
    key_roles: dict[str, list[str]] = {}
    for constraint in constraints:
        role = str(constraint.metadata.get("constraint_type") or "constraint")
        for column_name in constraint.metadata.get("columns", []):
            key_roles.setdefault(column_name, []).append(role)

    comment = node.metadata.get("comment")
    evidence = []
    if comment:
        evidence.append(
            {
                "kind": "database_comment",
                "source": "PostgreSQL catalog comment",
                "value": comment,
            }
        )
    if constraints:
        evidence.append(
            {
                "kind": "declared_constraints",
                "source": "pg_constraint",
                "stable_ids": [item.id for item in constraints],
            }
        )
    if foreign_targets or referenced_by:
        evidence.append(
            {
                "kind": "declared_relationships",
                "source": "PostgreSQL foreign keys",
                "outbound": [item.id for item in foreign_targets],
                "inbound": [item.id for item in referenced_by],
            }
        )

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
                "key_roles": sorted(key_roles.get(column.name or "", [])),
            }
            for column in columns
        ],
        "constraints": [
            {
                "id": item.id,
                "name": item.name,
                "type": item.metadata.get("constraint_type"),
                "columns": item.metadata.get("columns", []),
            }
            for item in constraints
        ],
        "indexes": [
            {
                "id": item.id,
                "name": item.name,
                "columns": item.metadata.get("columns", []),
                "is_unique": item.metadata.get("is_unique"),
            }
            for item in indexes
        ],
        "foreign_keys_to": [{"id": item.id, "label": item.label} for item in foreign_targets],
        "referenced_by": [{"id": item.id, "label": item.label} for item in referenced_by],
        "semantic_context": {
            "evidence": evidence,
            "source_of_truth_assessment": {
                "status": "unverified",
                "reason": "Catalog structure alone cannot establish business authority.",
                "signals": {
                    "has_primary_key": any(
                        item.metadata.get("constraint_type") == "PRIMARY KEY"
                        for item in constraints
                    ),
                    "inbound_foreign_keys": len(referenced_by),
                    "outbound_foreign_keys": len(foreign_targets),
                    "row_estimate": node.metadata.get("row_estimate"),
                },
            },
            "uncertainty": (
                "No approved business documentation is linked to this object."
                if not comment
                else "A database comment is available, but business ownership is not verified."
            ),
        },
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
