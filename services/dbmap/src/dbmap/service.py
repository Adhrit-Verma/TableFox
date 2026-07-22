from __future__ import annotations

from typing import Any, Protocol

from .explain import explain_object
from .graph import GraphEngine
from .models import GraphSnapshot
from .search import search_snapshot


class DatabaseSource(Protocol):
    def connectivity_check(self) -> dict[str, Any]: ...

    def snapshot(self, refresh: bool = False) -> GraphSnapshot: ...

    def readonly_query(self, sql: str, limit: int) -> dict[str, Any]: ...

    def explain_query(self, sql: str, include_plan: bool = False) -> dict[str, Any]: ...


class DatabaseMapService:
    """Application use cases shared by the HTTP and MCP adapters."""

    def __init__(self, introspector: DatabaseSource) -> None:
        self.introspector = introspector

    def connectivity_check(self) -> dict[str, Any]:
        return self.introspector.connectivity_check()

    def graph_snapshot(
        self,
        refresh: bool = False,
        schemas: list[str] | None = None,
        max_nodes: int | None = None,
    ) -> GraphSnapshot:
        if max_nodes is not None:
            max_nodes = max(1, min(max_nodes, 10000))
        snapshot = self.introspector.snapshot(refresh=refresh)
        return GraphEngine.filter_snapshot(snapshot, schemas=schemas, max_nodes=max_nodes)

    def search(self, query: str, limit: int = 25) -> list[dict]:
        snapshot = self.introspector.snapshot()
        return search_snapshot(snapshot, query, limit=max(1, min(limit, 100)))

    def neighbors(self, node_id: str, depth: int = 1, max_nodes: int = 100) -> GraphSnapshot:
        snapshot = self.introspector.snapshot()
        return GraphEngine.neighbors(
            snapshot,
            node_id,
            depth=max(0, min(depth, 5)),
            max_nodes=max(1, min(max_nodes, 1000)),
        )

    def explain_object(self, node_id: str) -> dict:
        return explain_object(self.introspector.snapshot(), node_id)

    def readonly_query(self, sql: str, limit: int = 200) -> dict[str, Any]:
        return self.introspector.readonly_query(sql, limit=max(1, limit))

    def explain_query(self, sql: str, include_plan: bool = False) -> dict[str, Any]:
        return self.introspector.explain_query(sql, include_plan=include_plan)
