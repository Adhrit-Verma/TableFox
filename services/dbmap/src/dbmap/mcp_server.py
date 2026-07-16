from __future__ import annotations

from typing import Any

from .explain import explain_object
from .graph import GraphEngine
from .postgres import PostgresIntrospector
from .search import search_snapshot


introspector = PostgresIntrospector()


def compact_snapshot(snapshot, max_nodes: int = 200) -> dict[str, Any]:
    max_nodes = max(1, min(max_nodes, 10000))
    limited = GraphEngine.filter_snapshot(snapshot, max_nodes=max_nodes)
    return {
        "database": limited.database,
        "generated_at": limited.generated_at,
        "summary": limited.summary,
        "nodes": [node.to_dict() for node in limited.nodes],
        "edges": [edge.to_dict() for edge in limited.edges],
    }


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("dbmap-postgres")

    @mcp.tool()
    def database_connectivity_check() -> dict[str, Any]:
        """Validate PostgreSQL credentials and report safe connection metadata."""
        return introspector.connectivity_check()

    @mcp.tool()
    def database_graph_snapshot(
        refresh: bool = False,
        schemas: list[str] | None = None,
        max_nodes: int = 200,
    ) -> dict[str, Any]:
        """Return a bounded graph snapshot of schemas, relations, columns, constraints, indexes, and relationships."""
        max_nodes = max(1, min(max_nodes, 10000))
        snapshot = introspector.snapshot(refresh=refresh)
        filtered = GraphEngine.filter_snapshot(snapshot, schemas=schemas, max_nodes=max_nodes)
        return compact_snapshot(filtered, max_nodes=max_nodes)

    @mcp.tool()
    def database_search(query: str, limit: int = 25) -> dict[str, Any]:
        """Search graph objects by table, column, constraint, index, comment, or data type."""
        snapshot = introspector.snapshot()
        return {
            "query": query,
            "results": search_snapshot(snapshot, query, limit=max(1, min(limit, 100))),
        }

    @mcp.tool()
    def database_neighbors(node_id: str, depth: int = 1, max_nodes: int = 100) -> dict[str, Any]:
        """Return nearby graph nodes and edges around one stable node ID."""
        depth = max(0, min(depth, 5))
        max_nodes = max(1, min(max_nodes, 1000))
        snapshot = introspector.snapshot()
        return compact_snapshot(GraphEngine.neighbors(snapshot, node_id, depth=depth, max_nodes=max_nodes), max_nodes)

    @mcp.tool()
    def database_explain_object(node_id: str) -> dict[str, Any]:
        """Summarize one graph object and its important columns and relationships."""
        snapshot = introspector.snapshot()
        return explain_object(snapshot, node_id)

    @mcp.tool()
    def database_readonly_query(sql: str, limit: int = 200) -> dict[str, Any]:
        """Run a guarded read-only SELECT/WITH query with timeout and row limit."""
        return introspector.readonly_query(sql, limit=max(1, limit))

    mcp.run()


if __name__ == "__main__":
    main()
