from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from pydantic import BaseModel, Field

from . import pages
from .service import DatabaseMapService, build_service
from .tenants import TenantError, TenantRegistry

service = build_service()

ICON_FILE = Path(__file__).with_name("static") / "icon.png"
SERVER_DESCRIPTION = (
    "Ask questions about your PostgreSQL database in plain language. TableFox finds the few "
    "tables a question needs and runs guarded, read-only queries."
)
class BatchQuery(BaseModel):
    """One named statement inside database_readonly_batch."""

    name: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
        description="Short identifier for this result, letters/digits/_/- only, e.g. top_products.",
    )
    sql: str = Field(description="One SELECT or WITH statement.")


def database_url_from_form(fields: dict[str, str]) -> str:
    """Build a postgresql:// URL from the connect form, escaping every part."""
    pasted = fields.get("database_url", "").strip()
    if pasted:
        return pasted
    host = fields.get("host", "").strip()
    if not host or any(char in host for char in "/@?#, "):
        raise TenantError("Enter a host name or address, e.g. db.example.com.")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 literal
    port = fields.get("port", "").strip() or "5432"
    if not port.isdigit():
        raise TenantError("The port must be a number.")
    user, dbname = fields.get("user", "").strip(), fields.get("dbname", "").strip()
    if not user or not dbname:
        raise TenantError("Enter the database name and the user.")
    sslmode = fields.get("sslmode", "require")
    secret = quote(fields.get("password", ""), safe="")
    credentials = f"{quote(user, safe='')}:{secret}" if secret else quote(user, safe="")
    return f"postgresql://{credentials}@{host}:{port}/{quote(dbname, safe='')}?sslmode={quote(sslmode, safe='')}"


SERVER_INSTRUCTIONS = (
    "Start with database_task_context for the user's question, then answer with at most five "
    "bounded SELECT/WITH statements in one database_readonly_batch call. Use only the joins it "
    "returns. TableFox is read-only: never attempt writes. Never ask the user to type database "
    "credentials into the chat; if no database is connected, give them the link from "
    "database_connections. If several databases are connected and the question does not say "
    "which, ask the user, then pass that name as the database argument."
)



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
    from mcp.types import Icon

    icons = [Icon(src=f"{public_url.rstrip('/')}/icon.png", mime_type="image/png", sizes=["512x512"])] if public_url else None
    mcp = MCPServer(
        "dbmap-postgres",
        title="TableFox",
        description=SERVER_DESCRIPTION,
        instructions=SERVER_INSTRUCTIONS,
        website_url="https://github.com/Adhrit-Verma/TableFox",
        icons=icons,
        **server_options,
    )

    @mcp.custom_route("/icon.png", methods=["GET"])
    async def icon(_request):
        from starlette.responses import FileResponse

        return FileResponse(ICON_FILE, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    # ChatGPT plugin review requires explicit booleans on every tool. All tools are
    # confined to the caller's one configured database, so openWorldHint is false.
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    setting = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

    def tool(annotations):
        """Register a tool whose ValueErrors (our own validation messages) reach the model as text."""

        def register(fn):
            @functools.wraps(fn)
            def guarded(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except ValueError as error:
                    raise ToolError(str(error)) from None

            return mcp.tool(annotations=annotations)(guarded)

        return register

    def connect_link(user_id: str) -> str:
        return f"{public_url.rstrip('/')}/connect?t={registry.store.make_link_token(user_id)}"

    def caller_id() -> str:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        if token is None or not token.subject:
            raise ToolError("Sign in to TableFox first.")
        return token.subject

    def current(database: str | None = None) -> tuple[DatabaseMapService, str]:
        """Resolve the caller's database; single-user mode ignores the name."""
        if registry is None:
            settings = single_service.settings
            return single_service, settings.mcp_actor if settings else "local-mcp"
        user_id = caller_id()
        names = registry.store.names(user_id)
        if not names:
            raise ToolError(
                "No database is connected yet. Open this private link to connect one "
                f"(valid for 10 minutes): {connect_link(user_id)}"
            )
        if database is None and len(names) > 1:
            raise ToolError(
                f"Several databases are connected: {', '.join(names)}. Ask the user which one "
                "to use, then call again with database set to that name."
            )
        name = (database or names[0]).strip().lower()
        if name not in names:
            raise ToolError(f"No database named '{name}'. Connected databases: {', '.join(names)}.")
        try:
            user_service = registry.service_for(user_id, name)
        except TenantError as error:
            raise ToolError(f"{error} Reconnect '{name}': {connect_link(user_id)}") from None
        return user_service, user_service.settings.mcp_actor

    @tool(read)
    def database_connectivity_check(database: str | None = None) -> dict[str, Any]:
        """Validate PostgreSQL credentials and report safe connection metadata."""
        tool_service, actor = current(database)
        return tool_service.connectivity_check(actor=actor)

    @tool(read)
    def database_graph_snapshot(
        refresh: bool = False,
        schemas: list[str] | None = None,
        max_nodes: int = 200,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded graph snapshot of schemas, relations, columns, constraints, indexes, and relationships."""
        tool_service, actor = current(database)
        return tool_service.graph_snapshot(
            refresh=refresh,
            schemas=schemas,
            max_nodes=max_nodes,
            actor=actor,
        ).to_dict()

    @tool(read)
    def database_search(query: str, limit: int = 25, database: str | None = None) -> dict[str, Any]:
        """Search graph objects by table, column, constraint, index, comment, or data type."""
        tool_service, actor = current(database)
        return {
            "query": query,
            "results": tool_service.search(query, limit=limit, actor=actor),
        }

    @tool(read)
    def database_neighbors(node_id: str, depth: int = 1, max_nodes: int = 100, database: str | None = None) -> dict[str, Any]:
        """Return nearby graph nodes and edges around one stable node ID."""
        tool_service, actor = current(database)
        return tool_service.neighbors(
            node_id,
            depth=depth,
            max_nodes=max_nodes,
            actor=actor,
        ).to_dict()

    @tool(read)
    def database_explain_object(node_id: str, database: str | None = None) -> dict[str, Any]:
        """Summarize one graph object and its important columns and relationships."""
        tool_service, actor = current(database)
        return tool_service.explain_object(node_id, actor=actor)

    @tool(read)
    def database_readonly_query(sql: str, limit: int = 200, database: str | None = None) -> dict[str, Any]:
        """Run a guarded read-only SELECT/WITH query with timeout and row limit."""
        tool_service, actor = current(database)
        return tool_service.readonly_query(sql, limit=limit, actor=actor)

    @tool(read)
    def database_readonly_batch(
        queries: list[BatchQuery],
        max_rows_each: int = 100,
        max_bytes: int = 32768,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Run up to five guarded reads in one transaction and return compact columnar rows.

        Each query needs a short identifier name (e.g. top_products) and one SELECT/WITH statement.
        """
        tool_service, actor = current(database)
        return tool_service.readonly_batch(
            [query.model_dump() for query in queries],
            max_rows_each=max_rows_each,
            max_bytes=max_bytes,
            actor=actor,
        )

    @tool(read)
    def database_explain_query(
        sql: str,
        include_plan: bool = False,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Plan a SELECT/WITH query without executing it and apply cost and row thresholds."""
        tool_service, actor = current(database)
        return tool_service.explain_query(sql, include_plan=include_plan, actor=actor)

    @tool(read)
    def database_find_join_path(
        source_id: str,
        target_id: str,
        max_hops: int = 6,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Find a bounded path backed by declared foreign keys or catalog dependencies."""
        tool_service, actor = current(database)
        return tool_service.join_path(
            source_id,
            target_id,
            max_hops=max_hops,
            actor=actor,
        )

    @tool(read)
    def database_source_of_truth(query: str, limit: int = 5, database: str | None = None) -> dict[str, Any]:
        """Rank authoritative candidates and distinguish verified context from heuristics."""
        tool_service, actor = current(database)
        return tool_service.source_of_truth(query, limit=limit, actor=actor)

    @tool(read)
    def database_task_context(
        question: str,
        max_relations: int = 6,
        max_bytes: int = 6144,
        refresh_context: bool = False,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Return a compact ranked schema and declared-join context for one task.

        When the context window is enabled, relations already sent this session come back
        in "known" instead of being repeated. Pass refresh_context=true to force full detail.
        """
        tool_service, actor = current(database)
        return tool_service.task_context(
            question,
            max_relations=max_relations,
            max_bytes=max_bytes,
            refresh_context=refresh_context,
            actor=actor,
        )

    @tool(setting)
    def database_context_window(size: int | None = None, database: str | None = None) -> dict[str, Any]:
        """Read or set how many relations are remembered as already-sent.

        Call with no size to see the current setting, the measured token savings, and the
        cost, then ask the user which value they want before setting it. Size 0 disables it.
        """
        tool_service, actor = current(database)
        if size is None:
            return tool_service.context_window_report()
        return tool_service.set_context_window(size, actor=actor)

    @tool(read)
    def database_schema_changes(database: str | None = None) -> dict[str, Any]:
        """Compare the configured baseline snapshot with the connected database."""
        tool_service, actor = current(database)
        return tool_service.schema_changes(actor=actor)

    @tool(read)
    def database_context_identity(database: str | None = None) -> dict[str, str]:
        """Return the safe database identity and schema fingerprint for context linking."""
        tool_service, actor = current(database)
        return tool_service.snapshot_identity(actor=actor)

    if registry is not None:

        @tool(read)
        def database_connections() -> dict[str, Any]:
            """List the user's connected databases by name, plus a private 10-minute link to add, replace, or remove one.

            Pass a name as the database argument of other tools when more than one is connected.
            Never ask the user to paste database credentials into the chat; send them this link.
            """
            user_id = caller_id()
            return {"databases": registry.store.names(user_id), "manage_url": connect_link(user_id)}

        @mcp.custom_route("/health", methods=["GET"])
        async def health(_request):
            from starlette.responses import JSONResponse

            return JSONResponse({"status": "ok"})

        @mcp.custom_route("/connect", methods=["GET", "POST"])
        async def connect_page(request):
            from starlette.responses import HTMLResponse

            def respond(markup: str, status: int = 200):
                return HTMLResponse(markup, status_code=status, headers=pages.PAGE_HEADERS)

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
                return respond(
                    pages.message_page(
                        "This link has expired",
                        "Connect links work once and expire after 10 minutes. Ask ChatGPT for a new TableFox link.",
                    ),
                    403,
                )
            user_id = link["sub"]
            if request.method == "POST":
                name = fields.get("name", "")
                try:
                    if fields.get("action") == "delete":
                        registry.forget(user_id, name)
                        message = f"Removed {name}."
                    else:
                        name = registry.connect(user_id, name, database_url_from_form(fields))
                        message = f"{name} is connected and verified as read-only."
                except TenantError as error:
                    kept = {key: fields.get(key, "") for key in ("name", "host", "port", "dbname", "user", "sslmode")}
                    names = registry.store.names(user_id)
                    return respond(pages.connect_page(token, names, kept, str(error)), 400)
                registry.store.consume_link(link)
                # A fresh single-use link lets the user add or remove another database.
                return respond(pages.done_page(message, registry.store.make_link_token(user_id)))
            return respond(pages.connect_page(token, registry.store.names(user_id)))

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
        os.getenv("DBMAP_TENANT_DB", "").strip() or base.cache_dir / "tenants.sqlite",
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
        public_host = os.getenv("DBMAP_MCP_PUBLIC_HOST", "").strip()
        mcp = create_mcp(public_url=f"https://{public_host}" if public_host else "")
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
        # Render and similar hosts assign the port through PORT.
        port=int(os.getenv("DBMAP_MCP_PORT") or os.getenv("PORT") or "8765"),
        transport_security=security,
    )


if __name__ == "__main__":
    main()
