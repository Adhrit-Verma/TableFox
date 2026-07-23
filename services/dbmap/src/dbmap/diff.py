from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import GraphEdge, GraphNode, GraphSnapshot


MAX_SNAPSHOT_BYTES = 50_000_000
VOLATILE_METADATA = {"context", "row_estimate", "usage"}


def schema_fingerprint(snapshot: GraphSnapshot) -> str:
    payload = {
        "nodes": [_stable_node(node) for node in sorted(snapshot.nodes, key=lambda item: item.id)],
        "edges": [edge.to_dict() for edge in sorted(snapshot.edges, key=lambda item: item.id)],
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compare_snapshots(before: GraphSnapshot, after: GraphSnapshot) -> dict[str, Any]:
    if before.database != after.database:
        raise ValueError("Baseline and current snapshot identify different databases.")
    before_nodes = {node.id: _stable_node(node) for node in before.nodes}
    after_nodes = {node.id: _stable_node(node) for node in after.nodes}
    before_edges = {edge.id: edge.to_dict() for edge in before.edges}
    after_edges = {edge.id: edge.to_dict() for edge in after.edges}

    added_nodes = sorted(after_nodes.keys() - before_nodes.keys())
    removed_nodes = sorted(before_nodes.keys() - after_nodes.keys())
    changed_nodes = sorted(
        node_id
        for node_id in before_nodes.keys() & after_nodes.keys()
        if before_nodes[node_id] != after_nodes[node_id]
    )
    added_edges = sorted(after_edges.keys() - before_edges.keys())
    removed_edges = sorted(before_edges.keys() - after_edges.keys())
    changed_edges = sorted(
        edge_id
        for edge_id in before_edges.keys() & after_edges.keys()
        if before_edges[edge_id] != after_edges[edge_id]
    )
    affected = set(added_nodes + removed_nodes + changed_nodes)
    dependencies_by_id = {}
    for snapshot in (before, after):
        for edge in snapshot.edges:
            if edge.kind not in {"foreign_key", "depends_on"}:
                continue
            if edge.source in affected or edge.target in affected:
                dependencies_by_id[edge.id] = {
                    "edge_id": edge.id,
                    "kind": edge.kind,
                    "source": edge.source,
                    "target": edge.target,
                    "evidence": "confirmed_catalog_dependency",
                }

    consumers_by_key = {}
    for snapshot in (before, after):
        for node in snapshot.nodes:
            if node.id not in affected:
                continue
            context = node.metadata.get("context", {})
            for item in context.get("consumers", []) + context.get("saved_queries", []):
                value = {"object_id": node.id, **item, "evidence": "approved_context"}
                key = json.dumps(value, sort_keys=True, default=str)
                consumers_by_key[key] = value

    return {
        "before_fingerprint": schema_fingerprint(before),
        "after_fingerprint": schema_fingerprint(after),
        "changed": bool(
            added_nodes
            or removed_nodes
            or changed_nodes
            or added_edges
            or removed_edges
            or changed_edges
        ),
        "nodes": {"added": added_nodes, "removed": removed_nodes, "changed": changed_nodes},
        "edges": {"added": added_edges, "removed": removed_edges, "changed": changed_edges},
        "impact": {
            "confirmed_dependencies": list(dependencies_by_id.values()),
            "documented_consumers": list(consumers_by_key.values()),
            "inferred_relationships": [],
        },
    }


def load_snapshot(path: Path) -> GraphSnapshot:
    if not path.is_file():
        raise ValueError(f"Baseline snapshot does not exist: {path}")
    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError("Baseline snapshot exceeds the 50 MB safety limit.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return GraphSnapshot(
        database=str(payload["database"]),
        generated_at=str(payload["generated_at"]),
        summary=dict(payload.get("summary", {})),
        nodes=[GraphNode(**node) for node in payload.get("nodes", [])],
        edges=[GraphEdge(**edge) for edge in payload.get("edges", [])],
    )


def _stable_node(node: GraphNode) -> dict[str, Any]:
    payload = node.to_dict()
    payload["metadata"] = {
        key: value for key, value in node.metadata.items() if key not in VOLATILE_METADATA
    }
    return payload
