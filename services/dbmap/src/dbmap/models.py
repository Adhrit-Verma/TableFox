from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str
    label: str
    schema: str | None = None
    name: str | None = None
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphEdge:
    id: str
    kind: str
    source: str
    target: str
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphSnapshot:
    database: str
    generated_at: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    summary: dict[str, int]

    @classmethod
    def create(
        cls,
        database: str,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> "GraphSnapshot":
        summary: dict[str, int] = {}
        for node in nodes:
            summary[node.kind] = summary.get(node.kind, 0) + 1
        summary["edges"] = len(edges)
        return cls(
            database=database,
            generated_at=datetime.now(timezone.utc).isoformat(),
            nodes=nodes,
            edges=edges,
            summary=summary,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }
