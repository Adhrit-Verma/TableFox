from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .service import DatabaseMapService, build_service
from .tenants import TenantError, TenantRegistry

service = build_service()

PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
}


class JwtVerifier:
    """Verify OAuth access tokens (e.g. Auth0) against the issuer's published keys."""

    def __init__(self, issuer: str, audience: str) -> None:
        import jwt

        self.issuer = issuer
        self.audience = audience
        self._jwks = jwt.PyJWKClient(issuer.rstrip("/") + "/.well-known/jwks.json", cache_keys=True)

    async def verify_token(self, token: str):
        import anyio
        import jwt
        from mcp.server.auth.provider import AccessToken

        try:
            key = await anyio.to_thread.run_sync(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or ""),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=str(claims["sub"]),
        )


def _page(title: str, body: str, status: int = 200):
    from starlette.responses import HTMLResponse

    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>TableFox - {html.escape(title)}</title>"
        "<body style='font-family:system-ui;max-width:34rem;margin:3rem auto;padding:0 1rem'>"
        f"<h1>{html.escape(title)}</h1>{body}</body>",
        status_code=status,
        headers=PAGE_HEADERS,
    )


def create_mcp(
    application_service: DatabaseMapService | None = None,
    *,
    registry: TenantRegistry | None = None,
    public_url: str = "",
    **server_options: Any,
):
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    single_service = application_service or service
    mcp = MCPServer("dbmap-postgres", **server_options)
    # ChatGPT plugin review requires explicit booleans on every tool. All tools are
    # confined to the caller's one configured database, so openWorldHint is false.
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    setting = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

    def connect_link(user_id: str) -> str:
        return f"{public_url.rstrip('/')}/connect?t={registry.store.make_link_token(user_id)}"

    def caller_id() -> str:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        if token is None or not token.subject:
            raise ToolError("Sign in to TableFox first.")
        return token.subject

    def current() -> tuple[DatabaseMapService, str]:
        if registry is None:
            settings = single_service.settings
            return single_service, settings.mcp_actor if settings else "local-mcp"
        user_id = caller_id()
        try:
            user_service = registry.service_for(user_id)
        except TenantError as error:
            raise ToolError(f"{error} Reconnect your database: {connect_link(user_id)}") from None
        if user_service is None:
            raise ToolError(
                "No database is connected yet. Open this private link to connect one "
                f"(valid for 10 minutes): {connect_link(user_id)}"
            )
        return user_service, user_service.settings.mcp_actor

    @mcp.tool(annotations=read)
    def database_connectivity_check() -> dict[str, Any]:
        """Validate PostgreSQL credentials and report safe connection metadata."""
        tool_service, actor = current()
        return tool_service.connectivity_check(actor=actor)

    @mcp.tool(annotations=read)
    def database_graph_snapshot(
        refresh: bool = False,
        schemas: list[str] | None = None,
        max_nodes: int = 200,
    ) -> dict[str, Any]:
        """Return a bounded graph snapshot of schemas, relations, columns, constraints, indexes, and relationships."""
        tool_service, actor = current()
        return tool_service.graph_snapshot(
            refresh=refresh,
            schemas=schemas,
            max_nodes=max_nodes,
            actor=actor,
        ).to_dict()

    @mcp.tool(annotations=read)
    def database_search(query: str, limit: int = 25) -> dict[str, Any]:
        """Search graph objects by table, column, constraint, index, comment, or data type."""
        tool_service, actor = current()
        return {
            "query": query,
            "results": tool_service.search(query, limit=limit, actor=actor),
        }

    @mcp.tool(annotations=read)
    def database_neighbors(node_id: str, depth: int = 1, max_nodes: int = 100) -> dict[str, Any]:
        """Return nearby graph nodes and edges around one stable node ID."""
        tool_service, actor = current()
        return tool_service.neighbors(
            node_id,
            depth=depth,
            max_nodes=max_nodes,
            actor=actor,
        ).to_dict()

    @mcp.tool(annotations=read)
    def database_explain_object(node_id: str) -> dict[str, Any]:
        """Summarize one graph object and its important columns and relationships."""
        tool_service, actor = current()
        return tool_service.explain_object(node_id, actor=actor)

    @mcp.tool(annotations=read)
    def database_readonly_query(sql: str, limit: int = 200) -> dict[str, Any]:
        """Run a guarded read-only SELECT/WITH query with timeout and row limit."""
        tool_service, actor = current()
        return tool_service.readonly_query(sql, limit=limit, actor=actor)

    @mcp.tool(annotations=read)
    def database_readonly_batch(
        queries: list[dict[str, Any]],
        max_rows_each: int = 100,
        max_bytes: int = 32768,
    ) -> dict[str, Any]:
        """Run up to five guarded reads in one transaction and return compact columnar rows."""
        tool_service, actor = current()
        return tool_service.readonly_batch(
            queries,
            max_rows_each=max_rows_each,
            max_bytes=max_bytes,
            actor=actor,
        )

    @mcp.tool(annotations=read)
    def database_explain_query(
        sql: str,
        include_plan: bool = False,
    ) -> dict[str, Any]:
        """Plan a SELECT/WITH query without executing it and apply cost and row thresholds."""
        tool_service, actor = current()
        return tool_service.explain_query(sql, include_plan=include_plan, actor=actor)

    @mcp.tool(annotations=read)
    def database_find_join_path(
        source_id: str,
        target_id: str,
        max_hops: int = 6,
    ) -> dict[str, Any]:
        """Find a bounded path backed by declared foreign keys or catalog dependencies."""
        tool_service, actor = current()
        return tool_service.join_path(
            source_id,
            target_id,
            max_hops=max_hops,
            actor=actor,
        )

    @mcp.tool(annotations=read)
    def database_source_of_truth(query: str, limit: int = 5) -> dict[str, Any]:
        """Rank authoritative candidates and distinguish verified context from heuristics."""
        tool_service, actor = current()
        return tool_service.source_of_truth(query, limit=limit, actor=actor)

    @mcp.tool(annotations=read)
    def database_task_context(
        question: str,
        max_relations: int = 6,
        max_bytes: int = 6144,
        refresh_context: bool = False,
    ) -> dict[str, Any]:
        """Return a compact ranked schema and declared-join context for one task.

        When the context window is enabled, relations already sent this session come back
        in "known" instead of being repeated. Pass refresh_context=true to force full detail.
        """
        tool_service, actor = current()
        return tool_service.task_context(
            question,
            max_relations=max_relations,
            max_bytes=max_bytes,
            refresh_context=refresh_context,
            actor=actor,
        )

    @mcp.tool(annotations=setting)
    def database_context_window(size: int | None = None) -> dict[str, Any]:
        """Read or set how many relations are remembered as already-sent.

        Call with no size to see the current setting, the measured token savings, and the
        cost, then ask the user which value they want before setting it. Size 0 disables it.
        """
        tool_service, actor = current()
        if size is None:
            return tool_service.context_window_report()
        return tool_service.set_context_window(size, actor=actor)

    @mcp.tool(annotations=read)
    def database_schema_changes() -> dict[str, Any]:
        """Compare the configured baseline snapshot with the connected database."""
        tool_service, actor = current()
        return tool_service.schema_changes(actor=actor)

    @mcp.tool(annotations=read)
    def database_context_identity() -> dict[str, str]:
        """Return the safe database identity and schema fingerprint for context linking."""
        tool_service, actor = current()
        return tool_service.snapshot_identity(actor=actor)

    if registry is not None:

        @mcp.tool(annotations=read)
        def database_connection_link() -> dict[str, str]:
            """Return a private 10-minute link where the user connects, replaces, or removes their database.

            Never ask the user to paste database credentials into the chat; send them this link.
            """
            return {"connect_url": connect_link(caller_id())}

        @mcp.custom_route("/health", methods=["GET"])
        async def health(_request):
            from starlette.responses import JSONResponse

            return JSONResponse({"status": "ok"})

        @mcp.custom_route("/connect", methods=["GET", "POST"])
        async def connect_page(request):
            if request.method == "GET":
                token = request.query_params.get("t", "")
                fields = {}
            else:
                body = (await request.body()).decode()
                fields = {key: values[0] for key, values in parse_qs(body).items()}
                token = fields.get("t", "")
            try:
                link = registry.store.read_link_token(token)
            except (TenantError, ValueError, KeyError):
                return _page("Link not valid", "<p>Ask ChatGPT for a new TableFox connect link.</p>", 403)
            safe_token = html.escape(token, quote=True)
            if request.method == "POST":
                try:
                    if fields.get("action") == "delete":
                        registry.forget(link["sub"])
                        message = "Your saved database connection was removed."
                    else:
                        registry.connect(link["sub"], fields.get("database_url", ""))
                        message = "Connected. Go back to ChatGPT and ask your question."
                except TenantError as error:
                    retry = f"<p><a href='/connect?t={safe_token}'>Try again</a></p>"
                    return _page("Not connected", f"<p>{html.escape(str(error))}</p>{retry}", 400)
                registry.store.consume_link(link)
                return _page("Done", f"<p>{html.escape(message)}</p>")
            hidden = f"<input type=hidden name=t value='{safe_token}'>"
            return _page(
                "Connect your database",
                "<p>Use a <b>read-only</b> PostgreSQL role reachable from the internet over SSL. "
                "Credentials are stored encrypted and are never shown to ChatGPT.</p>"
                f"<form method=post>{hidden}"
                "<label>Database URL<br><input name=database_url type=password required autocomplete=off "
                "placeholder='postgresql://reader:password@db.example.com:5432/mydb' style='width:100%'></label>"
                "<p><button>Connect</button></p></form>"
                f"<form method=post>{hidden}<input type=hidden name=action value=delete>"
                "<button>Remove my saved connection</button></form>",
            )

    return mcp


def create_multi_tenant_mcp():
    """Build the hosted server: OAuth-protected, one isolated database per user."""
    from mcp.server.auth.settings import AuthSettings

    from .tenants import CredentialStore

    def required(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise RuntimeError(f"{name} is required when DBMAP_MULTI_TENANT is on.")
        return value

    base = service.settings
    public_url = required("DBMAP_PUBLIC_URL").rstrip("/")
    issuer = required("DBMAP_OAUTH_ISSUER")
    audience = os.getenv("DBMAP_OAUTH_AUDIENCE", "").strip() or f"{public_url}/mcp"
    store = CredentialStore(
        Path(os.getenv("DBMAP_TENANT_DB", str(base.cache_dir / "tenants.sqlite"))),
        required("DBMAP_TENANT_KEY"),
    )
    ports = {int(port) for port in os.getenv("DBMAP_TENANT_PORTS", "5432").split(",") if port.strip()}
    registry = TenantRegistry(base, store, ports)
    return create_mcp(
        registry=registry,
        public_url=public_url,
        token_verifier=JwtVerifier(issuer, audience),
        auth=AuthSettings(
            issuer_url=issuer,
            resource_server_url=f"{public_url}/mcp",
            validate_token_resource=False,  # JwtVerifier checks the audience itself
        ),
    ), urlsplit(public_url).netloc


def main() -> None:
    from .config import _env_bool

    multi_tenant = _env_bool("DBMAP_MULTI_TENANT", False)
    if multi_tenant:
        mcp, public_host = create_multi_tenant_mcp()
        transport = "streamable-http"
    else:
        mcp = create_mcp()
        public_host = os.getenv("DBMAP_MCP_PUBLIC_HOST", "").strip()
        transport = os.getenv("DBMAP_MCP_TRANSPORT", "stdio")
    if transport != "streamable-http":
        mcp.run(transport=transport)
        return
    from mcp.server.transport_security import TransportSecuritySettings

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )
    if public_host:
        security.allowed_hosts.append(public_host)
        security.allowed_origins.append(f"https://{public_host}")
    mcp.run(
        transport="streamable-http",
        # Loopback by default: expose it only through an HTTPS proxy. A container sets
        # DBMAP_MCP_BIND=0.0.0.0 and publishes the port to the host's loopback only.
        host=os.getenv("DBMAP_MCP_BIND", "127.0.0.1"),
        port=int(os.getenv("DBMAP_MCP_PORT", "8765")),
        transport_security=security,
    )


if __name__ == "__main__":
    main()
