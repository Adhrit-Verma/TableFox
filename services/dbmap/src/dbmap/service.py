from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Protocol

from .audit import AuditLog
from .config import Settings
from .context import apply_context
from .diff import compare_snapshots, load_snapshot, schema_fingerprint
from .explain import explain_object
from .graph import GraphEngine
from .models import GraphSnapshot
from .search import search_snapshot


class DatabaseSource(Protocol):
    def connectivity_check(self) -> dict[str, Any]: ...

    def snapshot(self, refresh: bool = False) -> GraphSnapshot: ...

    def readonly_query(
        self,
        sql: str,
        limit: int,
        approved: bool = False,
    ) -> dict[str, Any]: ...

    def explain_query(self, sql: str, include_plan: bool = False) -> dict[str, Any]: ...


class DatabaseMapService:
    """Application use cases shared by the HTTP and MCP adapters."""

    def __init__(
        self,
        introspector: DatabaseSource,
        settings: Settings | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.introspector = introspector
        self.settings = settings or getattr(introspector, "settings", None)
        self.audit = audit

    def _snapshot(self, refresh: bool = False) -> GraphSnapshot:
        snapshot = self.introspector.snapshot(refresh=refresh)
        context_file = self.settings.context_file if self.settings else None
        return apply_context(snapshot, context_file)

    def _record(
        self,
        actor: str,
        action: str,
        *,
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.audit:
            self.audit.record(actor, action, target=target, details=details)

    def connectivity_check(self, actor: str = "local") -> dict[str, Any]:
        result = self.introspector.connectivity_check()
        self._record(actor, "connectivity_check")
        return result

    def graph_snapshot(
        self,
        refresh: bool = False,
        schemas: list[str] | None = None,
        max_nodes: int | None = None,
        actor: str = "local",
    ) -> GraphSnapshot:
        if max_nodes is not None:
            max_nodes = max(1, min(max_nodes, 10000))
        snapshot = self._snapshot(refresh=refresh)
        result = GraphEngine.filter_snapshot(snapshot, schemas=schemas, max_nodes=max_nodes)
        self._record(actor, "graph_snapshot", details={"nodes": len(result.nodes)})
        return result

    def search(self, query: str, limit: int = 25, actor: str = "local") -> list[dict]:
        result = search_snapshot(self._snapshot(), query, limit=max(1, min(limit, 100)))
        self._record(actor, "search", details={"result_count": len(result)})
        return result

    def neighbors(
        self,
        node_id: str,
        depth: int = 1,
        max_nodes: int = 100,
        actor: str = "local",
    ) -> GraphSnapshot:
        result = GraphEngine.neighbors(
            self._snapshot(),
            node_id,
            depth=max(0, min(depth, 5)),
            max_nodes=max(1, min(max_nodes, 1000)),
        )
        self._record(actor, "neighbors", target=node_id, details={"nodes": len(result.nodes)})
        return result

    def explain_object(self, node_id: str, actor: str = "local") -> dict:
        result = explain_object(self._snapshot(), node_id)
        self._record(actor, "explain_object", target=node_id, details={"found": result["found"]})
        return result

    def readonly_query(
        self,
        sql: str,
        limit: int = 200,
        approved: bool = False,
        actor: str = "local",
    ) -> dict[str, Any]:
        result = self.introspector.readonly_query(
            sql,
            limit=max(1, limit),
            approved=approved,
        )
        self._record(
            actor,
            "readonly_query",
            details={
                "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                "blocked": result.get("blocked", False),
                "row_count": result.get("row_count", 0),
                "approved": approved,
            },
        )
        return result

    def explain_query(
        self,
        sql: str,
        include_plan: bool = False,
        actor: str = "local",
    ) -> dict[str, Any]:
        result = self.introspector.explain_query(sql, include_plan=include_plan)
        self._record(
            actor,
            "explain_query",
            details={
                "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                "within_policy": result["within_policy"],
            },
        )
        return result

    def join_path(
        self,
        source_id: str,
        target_id: str,
        max_hops: int = 6,
        actor: str = "local",
    ) -> dict[str, Any]:
        result = GraphEngine.join_path(self._snapshot(), source_id, target_id, max_hops)
        self._record(
            actor,
            "join_path",
            target=f"{source_id}->{target_id}",
            details={"found": result["found"]},
        )
        return result

    def source_of_truth(
        self,
        query: str,
        limit: int = 5,
        actor: str = "local",
    ) -> dict[str, Any]:
        snapshot = self._snapshot()
        candidates = []
        for result in search_snapshot(snapshot, query, limit=max(1, min(limit * 4, 100))):
            if result["kind"] not in {"table", "view", "materialized_view"}:
                continue
            explanation = explain_object(snapshot, result["id"])
            assessment = explanation["semantic_context"]["source_of_truth_assessment"]
            candidates.append(
                {
                    "id": result["id"],
                    "label": result["label"],
                    "status": assessment["status"],
                    "evidence": explanation["semantic_context"]["evidence"],
                    "signals": assessment["signals"],
                    "uncertainty": explanation["semantic_context"]["uncertainty"],
                }
            )
            if len(candidates) >= max(1, min(limit, 20)):
                break
        candidates.sort(key=lambda item: item["status"] != "verified")
        self._record(actor, "source_of_truth", details={"candidate_count": len(candidates)})
        return {
            "query": query,
            "candidates": candidates,
            "resolved": bool(candidates and candidates[0]["status"] == "verified"),
            "next_step": (
                None
                if candidates and candidates[0]["status"] == "verified"
                else "Confirm the owner and add approved context for the authoritative object."
            ),
        }

    def schema_changes(
        self,
        baseline: Path | None = None,
        actor: str = "local",
    ) -> dict[str, Any]:
        baseline_path = baseline or (self.settings.baseline_file if self.settings else None)
        if not baseline_path:
            raise ValueError("No baseline snapshot is configured.")
        before = apply_context(
            load_snapshot(baseline_path),
            self.settings.context_file if self.settings else None,
        )
        after = self._snapshot(refresh=True)
        result = compare_snapshots(before, after)
        self._record(actor, "schema_changes", target=str(baseline_path), details={"changed": result["changed"]})
        return result

    def snapshot_identity(self, actor: str = "local") -> dict[str, str]:
        snapshot = self._snapshot()
        result = {
            "database": snapshot.database,
            "schema_fingerprint": schema_fingerprint(snapshot),
        }
        self._record(actor, "context_identity")
        return result


def build_service(settings: Settings | None = None) -> DatabaseMapService:
    from .postgres import PostgresIntrospector

    configured = settings or Settings.from_env()
    return DatabaseMapService(
        PostgresIntrospector(configured),
        settings=configured,
        audit=AuditLog(configured.audit_dir, configured.audit_retention_days),
    )
