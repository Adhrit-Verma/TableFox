from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .models import GraphSnapshot
from .config import Settings
from .security import ApiKeyAuth, Principal
from .service import build_service


logger = logging.getLogger(__name__)
app = FastAPI(title="Database Graph Map", version="0.1.0")
settings = Settings.from_env()
service = build_service(settings)
auth = ApiKeyAuth(settings.auth_file, settings.auth_required)
BROWSER_ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000"}


class ExplainQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    include_plan: bool = False


class ReadonlyQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    limit: int = Field(default=200, ge=1, le=10_000)
    approved: bool = False


class JoinPathRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=1000)
    target_id: str = Field(min_length=1, max_length=1000)
    max_hops: int = Field(default=6, ge=1, le=12)


def require_scope(scope: str):
    def dependency(
        authorization: Annotated[str | None, Header()] = None,
    ) -> Principal:
        try:
            principal = auth.authenticate(authorization)
        except (PermissionError, ValueError, OSError, json.JSONDecodeError) as error:
            audit_log = getattr(service, "audit", None)
            if audit_log:
                audit_log.record(
                    "unknown",
                    "api_authentication",
                    outcome="denied",
                    details={"scope": scope},
                )
            raise HTTPException(status_code=401, detail=str(error)) from error
        if not principal.can(scope):
            audit_log = getattr(service, "audit", None)
            if audit_log:
                audit_log.record(
                    principal.name,
                    "api_authorization",
                    outcome="denied",
                    details={"scope": scope, "role": principal.role},
                )
            raise HTTPException(status_code=403, detail="This API key lacks the required role.")
        return principal

    return dependency


MetadataPrincipal = Annotated[Principal, Depends(require_scope("metadata"))]
ExplainPrincipal = Annotated[Principal, Depends(require_scope("explain"))]
WorkflowPrincipal = Annotated[Principal, Depends(require_scope("workflow"))]
QueryPrincipal = Annotated[Principal, Depends(require_scope("query"))]

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(BROWSER_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
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
    principal: MetadataPrincipal,
    refresh: bool = False,
    schemas: Annotated[list[str] | None, Query()] = None,
    max_nodes: int | None = Query(default=None, ge=1, le=10000),
) -> dict:
    return service.graph_snapshot(
        refresh=refresh,
        schemas=schemas,
        max_nodes=max_nodes,
        actor=principal.name,
    ).to_dict()


@app.get("/graph/search")
def graph_search(
    principal: MetadataPrincipal,
    q: str,
    limit: int = Query(default=25, ge=1, le=100),
) -> dict:
    return {"query": q, "results": service.search(q, limit=limit, actor=principal.name)}


@app.get("/graph/node/{node_id:path}")
def graph_node(node_id: str, principal: MetadataPrincipal) -> dict:
    return service.explain_object(node_id, actor=principal.name)


@app.post("/query/explain")
def query_explain(request: ExplainQueryRequest, principal: ExplainPrincipal) -> dict:
    try:
        return service.explain_query(
            request.sql,
            include_plan=request.include_plan,
            actor=principal.name,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # pragma: no cover - depends on external database
        logger.exception("PostgreSQL query planning failed")
        raise HTTPException(
            status_code=400,
            detail="PostgreSQL could not plan the query.",
        ) from error


@app.post("/query/readonly")
def query_readonly(request: ReadonlyQueryRequest, principal: QueryPrincipal) -> dict:
    if request.approved and not principal.can("approve"):
        raise HTTPException(status_code=403, detail="Only an admin can approve high-risk queries.")
    try:
        return service.readonly_query(
            request.sql,
            limit=request.limit,
            approved=request.approved,
            actor=principal.name,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/workflow/join-path")
def workflow_join_path(request: JoinPathRequest, principal: WorkflowPrincipal) -> dict:
    return service.join_path(
        request.source_id,
        request.target_id,
        max_hops=request.max_hops,
        actor=principal.name,
    )


@app.get("/workflow/source-of-truth")
def workflow_source_of_truth(
    principal: WorkflowPrincipal,
    q: str,
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    return service.source_of_truth(q, limit=limit, actor=principal.name)


@app.get("/graph/changes")
def graph_changes(principal: WorkflowPrincipal) -> dict:
    try:
        return service.schema_changes(actor=principal.name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/graph/identity")
def graph_identity(principal: MetadataPrincipal) -> dict:
    return service.snapshot_identity(actor=principal.name)


@app.websocket("/graph/live")
async def graph_live(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin and origin not in BROWSER_ORIGINS:
        await websocket.close(code=1008, reason="Origin is not allowed.")
        return
    authorization = websocket.headers.get("authorization")
    protocols = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    if not authorization and len(protocols) == 2 and protocols[0] == "tablefox":
        authorization = f"Bearer {protocols[1]}"
    try:
        principal = auth.authenticate(authorization)
        if not principal.can("metadata"):
            raise PermissionError("This API key lacks the required role.")
    except (PermissionError, ValueError, OSError, json.JSONDecodeError):
        audit_log = getattr(service, "audit", None)
        if audit_log:
            audit_log.record(
                "unknown",
                "websocket_authentication",
                outcome="denied",
            )
        await websocket.close(code=1008, reason="Authentication failed.")
        return
    await websocket.accept(subprotocol="tablefox" if protocols and protocols[0] == "tablefox" else None)
    last_signature: str | None = None
    try:
        while True:
            try:
                snapshot = await asyncio.to_thread(
                    service.graph_snapshot,
                    refresh=True,
                    actor=principal.name,
                )
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
