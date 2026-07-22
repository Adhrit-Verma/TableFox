from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .models import GraphSnapshot
from .postgres import PostgresIntrospector
from .service import DatabaseMapService


logger = logging.getLogger(__name__)
app = FastAPI(title="Database Graph Map", version="0.1.0")
service = DatabaseMapService(PostgresIntrospector())
BROWSER_ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000"}


class ExplainQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    include_plan: bool = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(BROWSER_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _structure_signature(snapshot: GraphSnapshot) -> str:
    digest = hashlib.sha256()
    for node in snapshot.nodes:
        digest.update(node.id.encode("utf-8"))
        digest.update(b"\0")
    for edge in snapshot.edges:
        digest.update(edge.id.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


@app.get("/health")
def health() -> JSONResponse:
    try:
        check = service.connectivity_check()
        return JSONResponse(content={"ok": True, "database": check})
    except Exception:  # pragma: no cover - depends on external database
        logger.exception("PostgreSQL health check failed")
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "PostgreSQL is unavailable."},
        )


@app.get("/graph")
def graph(
    refresh: bool = False,
    schemas: Annotated[list[str] | None, Query()] = None,
    max_nodes: int | None = Query(default=None, ge=1, le=10000),
) -> dict:
    return service.graph_snapshot(
        refresh=refresh,
        schemas=schemas,
        max_nodes=max_nodes,
    ).to_dict()


@app.get("/graph/search")
def graph_search(q: str, limit: int = Query(default=25, ge=1, le=100)) -> dict:
    return {"query": q, "results": service.search(q, limit=limit)}


@app.get("/graph/node/{node_id:path}")
def graph_node(node_id: str) -> dict:
    return service.explain_object(node_id)


@app.post("/query/explain")
def query_explain(request: ExplainQueryRequest) -> dict:
    try:
        return service.explain_query(request.sql, include_plan=request.include_plan)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover - depends on external database
        logger.exception("PostgreSQL query planning failed")
        raise HTTPException(
            status_code=400,
            detail="PostgreSQL could not plan the query.",
        ) from error


@app.websocket("/graph/live")
async def graph_live(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin and origin not in BROWSER_ORIGINS:
        await websocket.close(code=1008, reason="Origin is not allowed.")
        return
    await websocket.accept()
    last_signature: str | None = None
    try:
        while True:
            try:
                snapshot = await asyncio.to_thread(service.graph_snapshot, refresh=True)
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
            except WebSocketDisconnect:
                raise
            except Exception:
                logger.exception("Live graph refresh failed")
                await websocket.send_json(
                    {"type": "graph_error", "error": "Live graph refresh failed."}
                )
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        return
