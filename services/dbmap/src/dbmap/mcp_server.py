from __future__ import annotations

from typing import Any

from .service import DatabaseMapService, build_service


service = build_service()


def create_mcp(application_service: DatabaseMapService | None = None):
    from mcp.server.fastmcp import FastMCP

    tool_service = application_service or service
    actor = tool_service.settings.mcp_actor if tool_service.settings else "local-mcp"
    mcp = FastMCP("dbmap-postgres")

    @mcp.tool()
    def database_connectivity_check() -> dict[str, Any]:
        """Validate PostgreSQL credentials and report safe connection metadata."""
        return tool_service.connectivity_check(actor=actor)

    @mcp.tool()
    def database_graph_snapshot(
        refresh: bool = False,
        schemas: list[str] | None = None,
        max_nodes: int = 200,
    ) -> dict[str, Any]:
        """Return a bounded graph snapshot of schemas, relations, columns, constraints, indexes, and relationships."""
        return tool_service.graph_snapshot(
            refresh=refresh,
            schemas=schemas,
            max_nodes=max_nodes,
            actor=actor,
        ).to_dict()

    @mcp.tool()
    def database_search(query: str, limit: int = 25) -> dict[str, Any]:
        """Search graph objects by table, column, constraint, index, comment, or data type."""
        return {
            "query": query,
            "results": tool_service.search(query, limit=limit, actor=actor),
        }

    @mcp.tool()
    def database_neighbors(node_id: str, depth: int = 1, max_nodes: int = 100) -> dict[str, Any]:
        """Return nearby graph nodes and edges around one stable node ID."""
        return tool_service.neighbors(
            node_id,
            depth=depth,
            max_nodes=max_nodes,
            actor=actor,
        ).to_dict()

    @mcp.tool()
    def database_explain_object(node_id: str) -> dict[str, Any]:
        """Summarize one graph object and its important columns and relationships."""
        return tool_service.explain_object(node_id, actor=actor)

    @mcp.tool()
    def database_readonly_query(sql: str, limit: int = 200) -> dict[str, Any]:
        """Run a guarded read-only SELECT/WITH query with timeout and row limit."""
        return tool_service.readonly_query(sql, limit=limit, actor=actor)

    @mcp.tool()
    def database_explain_query(
        sql: str,
        include_plan: bool = False,
    ) -> dict[str, Any]:
        """Plan a SELECT/WITH query without executing it and apply cost and row thresholds."""
        return tool_service.explain_query(sql, include_plan=include_plan, actor=actor)

    @mcp.tool()
    def database_find_join_path(
        source_id: str,
        target_id: str,
        max_hops: int = 6,
    ) -> dict[str, Any]:
        """Find a bounded path backed by declared foreign keys or catalog dependencies."""
        return tool_service.join_path(
            source_id,
            target_id,
            max_hops=max_hops,
            actor=actor,
        )

    @mcp.tool()
    def database_source_of_truth(query: str, limit: int = 5) -> dict[str, Any]:
        """Rank authoritative candidates and distinguish verified context from heuristics."""
        return tool_service.source_of_truth(query, limit=limit, actor=actor)

    @mcp.tool()
    def database_schema_changes() -> dict[str, Any]:
        """Compare the configured baseline snapshot with the connected database."""
        return tool_service.schema_changes(actor=actor)

    @mcp.tool()
    def database_context_identity() -> dict[str, str]:
        """Return the safe database identity and schema fingerprint for context linking."""
        return tool_service.snapshot_identity(actor=actor)

    return mcp


def main() -> None:
    mcp = create_mcp()

    mcp.run()


if __name__ == "__main__":
    main()
