from __future__ import annotations

import asyncio
import hashlib
from typing import Annotated

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .explain import explain_object
from .graph import GraphEngine
from .postgres import PostgresIntrospector
from .search import search_snapshot


app = FastAPI(title="Database Graph Map", version="0.1.0")
introspector = PostgresIntrospector()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _structure_signature(snapshot) -> str:
    digest = hashlib.sha256()
    for node in snapshot.nodes:
        digest.update(node.id.encode("utf-8"))
        digest.update(b"\0")
    for edge in snapshot.edges:
        digest.update(edge.id.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


@app.get("/health")
def health() -> dict:
    try:
        check = introspector.connectivity_check()
        return {"ok": True, "database": check}
    except Exception as exc:  # pragma: no cover - depends on external database
        return {"ok": False, "error": str(exc)}


@app.get("/graph")
def graph(
    refresh: bool = False,
    schemas: Annotated[list[str] | None, Query()] = None,
    max_nodes: int | None = Query(default=None, ge=1, le=10000),
) -> dict:
    snapshot = introspector.snapshot(refresh=refresh)
    filtered = GraphEngine.filter_snapshot(snapshot, schemas=schemas, max_nodes=max_nodes)
    return filtered.to_dict()


@app.get("/graph/search")
def graph_search(q: str, limit: int = Query(default=25, ge=1, le=100)) -> dict:
    snapshot = introspector.snapshot()
    return {"query": q, "results": search_snapshot(snapshot, q, limit=limit)}


@app.get("/graph/node/{node_id:path}")
def graph_node(node_id: str) -> dict:
    snapshot = introspector.snapshot()
    return explain_object(snapshot, node_id)


@app.websocket("/graph/live")
async def graph_live(websocket: WebSocket) -> None:
    await websocket.accept()
    last_signature: str | None = None
    try:
        while True:
            try:
                snapshot = introspector.snapshot(refresh=True)
                signature = _structure_signature(snapshot)
                if signature != last_signature:
                    await websocket.send_json(
                        {
                            "type": "graph_snapshot",
                            "summary": snapshot.summary,
                            "generated_at": snapshot.generated_at,
                            "graph": snapshot.to_dict(),
                        }
                    )
                    last_signature = signature
                else:
                    await websocket.send_json(
                        {
                            "type": "graph_heartbeat",
                            "summary": snapshot.summary,
                            "generated_at": snapshot.generated_at,
                        }
                    )
            except Exception as exc:
                await websocket.send_json({"type": "graph_error", "error": str(exc)})
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        return
