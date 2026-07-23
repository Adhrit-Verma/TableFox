from __future__ import annotations

from collections import deque
from typing import Iterable

from .ids import edge_id, node_id
from .models import GraphEdge, GraphNode, GraphSnapshot


SYSTEM_SCHEMAS = {"pg_catalog", "information_schema"}


class GraphEngine:
    """Builds and queries a typed database graph from PostgreSQL metadata rows."""

    def __init__(self, database_label: str = "postgres") -> None:
        self.database_label = database_label

    def build(self, metadata: dict[str, list[dict]]) -> GraphSnapshot:
        nodes_by_id: dict[str, GraphNode] = {}
        edges_by_id: dict[str, GraphEdge] = {}

        def add_node(node: GraphNode) -> None:
            nodes_by_id[node.id] = node

        def add_edge(edge: GraphEdge) -> None:
            if edge.source in nodes_by_id and edge.target in nodes_by_id:
                edges_by_id[edge.id] = edge

        for schema in sorted({row["schema"] for row in metadata.get("relations", [])}):
            schema_node = GraphNode(
                id=node_id("schema", schema),
                kind="schema",
                label=schema,
                schema=schema,
                name=schema,
            )
            add_node(schema_node)

        usage_by_relation = {
            (row["schema"], row["name"]): row for row in metadata.get("usage", [])
        }
        for relation in metadata.get("relations", []):
            schema = relation["schema"]
            name = relation["name"]
            kind = relation.get("kind", "table")
            rel_id = node_id(kind, schema, name)
            schema_id = node_id("schema", schema)
            usage = usage_by_relation.get((schema, name))
            add_node(
                GraphNode(
                    id=rel_id,
                    kind=kind,
                    label=f"{schema}.{name}",
                    schema=schema,
                    name=name,
                    parent_id=schema_id,
                    metadata={
                        "comment": relation.get("comment"),
                        "row_estimate": relation.get("row_estimate"),
                        "usage": _usage_metadata(
                            usage,
                            kind,
                            str(metadata.get("usage_status", "unavailable")),
                        ),
                    },
                )
            )
            add_edge(
                GraphEdge(
                    id=edge_id("contains", schema_id, rel_id),
                    kind="contains",
                    source=schema_id,
                    target=rel_id,
                    label="contains",
                )
            )

        relation_kind_by_key = {
            (node.schema, node.name): node.kind
            for node in nodes_by_id.values()
            if node.kind in {"table", "view", "materialized_view"}
        }

        for column in metadata.get("columns", []):
            schema = column["schema"]
            table = column["table"]
            relation_kind = relation_kind_by_key.get((schema, table), "table")
            rel_id = node_id(relation_kind, schema, table)
            col_id = node_id("column", schema, table, column["name"])
            add_node(
                GraphNode(
                    id=col_id,
                    kind="column",
                    label=column["name"],
                    schema=schema,
                    name=column["name"],
                    parent_id=rel_id,
                    metadata={
                        "table": table,
                        "ordinal_position": column.get("ordinal_position"),
                        "data_type": column.get("data_type"),
                        "is_nullable": column.get("is_nullable"),
                        "default": column.get("default"),
                        "comment": column.get("comment"),
                    },
                )
            )
            add_edge(
                GraphEdge(
                    id=edge_id("has_column", rel_id, col_id),
                    kind="has_column",
                    source=rel_id,
                    target=col_id,
                    label="column",
                )
            )

        for constraint in metadata.get("constraints", []):
            schema = constraint["schema"]
            table = constraint["table"]
            relation_kind = relation_kind_by_key.get((schema, table), "table")
            rel_id = node_id(relation_kind, schema, table)
            constraint_id = node_id("constraint", schema, table, constraint["name"])
            columns = constraint.get("columns", [])
            add_node(
                GraphNode(
                    id=constraint_id,
                    kind="constraint",
                    label=constraint["name"],
                    schema=schema,
                    name=constraint["name"],
                    parent_id=rel_id,
                    metadata={
                        "table": table,
                        "constraint_type": constraint.get("type"),
                        "columns": columns,
                        "foreign_schema": constraint.get("foreign_schema"),
                        "foreign_table": constraint.get("foreign_table"),
                        "foreign_columns": constraint.get("foreign_columns", []),
                    },
                )
            )
            add_edge(
                GraphEdge(
                    id=edge_id("has_constraint", rel_id, constraint_id),
                    kind="has_constraint",
                    source=rel_id,
                    target=constraint_id,
                    label=constraint.get("type"),
                )
            )
            for column_name in columns:
                col_id = node_id("column", schema, table, column_name)
                add_edge(
                    GraphEdge(
                        id=edge_id("constraint_column", constraint_id, col_id),
                        kind="constraint_column",
                        source=constraint_id,
                        target=col_id,
                        label="uses",
                    )
                )

            if constraint.get("type") == "FOREIGN KEY":
                foreign_schema = constraint.get("foreign_schema")
                foreign_table = constraint.get("foreign_table")
                if foreign_schema and foreign_table:
                    target_kind = relation_kind_by_key.get((foreign_schema, foreign_table), "table")
                    target_id = node_id(target_kind, foreign_schema, foreign_table)
                    add_edge(
                        GraphEdge(
                            id=edge_id("foreign_key", rel_id, target_id),
                            kind="foreign_key",
                            source=rel_id,
                            target=target_id,
                            label=constraint["name"],
                            metadata={
                                "columns": columns,
                                "foreign_columns": constraint.get("foreign_columns", []),
                                "constraint": constraint["name"],
                            },
                        )
                    )

        for index in metadata.get("indexes", []):
            schema = index["schema"]
            table = index["table"]
            relation_kind = relation_kind_by_key.get((schema, table), "table")
            rel_id = node_id(relation_kind, schema, table)
            index_id = node_id("index", schema, table, index["name"])
            add_node(
                GraphNode(
                    id=index_id,
                    kind="index",
                    label=index["name"],
                    schema=schema,
                    name=index["name"],
                    parent_id=rel_id,
                    metadata={
                        "table": table,
                        "is_unique": index.get("is_unique"),
                        "is_primary": index.get("is_primary"),
                        "columns": index.get("columns", []),
                        "definition": index.get("definition"),
                    },
                )
            )
            add_edge(
                GraphEdge(
                    id=edge_id("has_index", rel_id, index_id),
                    kind="has_index",
                    source=rel_id,
                    target=index_id,
                    label="index",
                )
            )

        for dependency in metadata.get("dependencies", []):
            source_key = (dependency["schema"], dependency["name"])
            target_key = (dependency["target_schema"], dependency["target_name"])
            source_kind = relation_kind_by_key.get(source_key)
            target_kind = relation_kind_by_key.get(target_key)
            if not source_kind or not target_kind:
                continue
            source_id = node_id(source_kind, *source_key)
            target_id = node_id(target_kind, *target_key)
            add_edge(
                GraphEdge(
                    id=edge_id("depends_on", source_id, target_id),
                    kind="depends_on",
                    source=source_id,
                    target=target_id,
                    label="depends on",
                    metadata={"evidence": "pg_depend"},
                )
            )

        return GraphSnapshot.create(
            database=self.database_label,
            nodes=sorted(nodes_by_id.values(), key=lambda node: node.id),
            edges=sorted(edges_by_id.values(), key=lambda edge: edge.id),
        )

    @staticmethod
    def filter_snapshot(
        snapshot: GraphSnapshot,
        schemas: Iterable[str] | None = None,
        max_nodes: int | None = None,
    ) -> GraphSnapshot:
        schema_set = {schema for schema in schemas or [] if schema}
        nodes = [
            node
            for node in snapshot.nodes
            if not schema_set or node.schema in schema_set
        ]
        if max_nodes is not None:
            kind_priority = {
                "schema": 0,
                "table": 1,
                "view": 1,
                "materialized_view": 1,
                "constraint": 2,
                "index": 3,
                "column": 4,
            }
            nodes = sorted(
                nodes,
                key=lambda node: (kind_priority.get(node.kind, 5), node.id),
            )[: max(1, max_nodes)]
        node_ids = {node.id for node in nodes}
        edges = [
            edge
            for edge in snapshot.edges
            if edge.source in node_ids and edge.target in node_ids
        ]
        return GraphSnapshot.create(snapshot.database, nodes, edges)

    @staticmethod
    def neighbors(snapshot: GraphSnapshot, node: str, depth: int = 1, max_nodes: int = 100) -> GraphSnapshot:
        depth = max(0, depth)
        max_nodes = max(1, max_nodes)
        node_by_id = {item.id: item for item in snapshot.nodes}
        if node not in node_by_id:
            return GraphSnapshot.create(snapshot.database, [], [])

        adjacency: dict[str, set[str]] = {}
        for edge in snapshot.edges:
            adjacency.setdefault(edge.source, set()).add(edge.target)
            adjacency.setdefault(edge.target, set()).add(edge.source)

        seen = {node}
        queue: deque[tuple[str, int]] = deque([(node, 0)])
        while queue and len(seen) < max_nodes:
            current, distance = queue.popleft()
            if distance >= depth:
                continue
            for neighbor in sorted(adjacency.get(current, set())):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, distance + 1))
                if len(seen) >= max_nodes:
                    break

        nodes = [item for item in snapshot.nodes if item.id in seen]
        edges = [
            edge
            for edge in snapshot.edges
            if edge.source in seen and edge.target in seen
        ]
        return GraphSnapshot.create(snapshot.database, nodes, edges)

    @staticmethod
    def join_path(snapshot: GraphSnapshot, source: str, target: str, max_hops: int = 6) -> dict:
        relation_ids = {
            node.id
            for node in snapshot.nodes
            if node.kind in {"table", "view", "materialized_view"}
        }
        if source not in relation_ids or target not in relation_ids:
            return {"found": False, "reason": "Both IDs must identify visible relations."}

        edges = [
            edge
            for edge in snapshot.edges
            if edge.kind in {"foreign_key", "depends_on"}
            and edge.source in relation_ids
            and edge.target in relation_ids
        ]
        adjacency: dict[str, list[tuple[str, GraphEdge]]] = {}
        for edge in edges:
            adjacency.setdefault(edge.source, []).append((edge.target, edge))
            adjacency.setdefault(edge.target, []).append((edge.source, edge))

        queue: deque[tuple[str, list[str], list[GraphEdge]]] = deque([(source, [source], [])])
        seen = {source}
        while queue:
            current, node_path, edge_path = queue.popleft()
            if current == target:
                return {
                    "found": True,
                    "verified": True,
                    "nodes": node_path,
                    "edges": [edge.to_dict() for edge in edge_path],
                    "evidence": "declared_foreign_keys_and_catalog_dependencies",
                }
            if len(edge_path) >= max(1, min(max_hops, 12)):
                continue
            for neighbor, edge in sorted(adjacency.get(current, []), key=lambda item: item[0]):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, [*node_path, neighbor], [*edge_path, edge]))
        return {"found": False, "reason": "No declared relationship path was found."}

    @staticmethod
    def validate_relation_set(snapshot: GraphSnapshot, relation_ids: set[str]) -> dict:
        if len(relation_ids) < 2:
            return {"verified": True, "relations": sorted(relation_ids), "edges": []}
        relevant = [
            edge
            for edge in snapshot.edges
            if edge.kind in {"foreign_key", "depends_on"}
            and edge.source in relation_ids
            and edge.target in relation_ids
        ]
        adjacency: dict[str, set[str]] = {}
        for edge in relevant:
            adjacency.setdefault(edge.source, set()).add(edge.target)
            adjacency.setdefault(edge.target, set()).add(edge.source)
        seen = {min(relation_ids)}
        queue = deque(seen)
        while queue:
            current = queue.popleft()
            for neighbor in adjacency.get(current, set()) - seen:
                seen.add(neighbor)
                queue.append(neighbor)
        return {
            "verified": seen == relation_ids,
            "relations": sorted(relation_ids),
            "edges": [edge.to_dict() for edge in relevant],
            "unconnected": sorted(relation_ids - seen),
        }


def _usage_metadata(usage: dict | None, kind: str, status: str) -> dict:
    if usage:
        return {
            "status": "available",
            "sequential_scans": usage.get("sequential_scans"),
            "index_scans": usage.get("index_scans"),
            "live_rows": usage.get("live_rows"),
            "last_analyze": usage.get("last_analyze"),
            "stats_reset": usage.get("stats_reset"),
        }
    if kind in {"view", "materialized_view"}:
        return {"status": "not_applicable"}
    return {"status": "unavailable" if status == "available" else status}
