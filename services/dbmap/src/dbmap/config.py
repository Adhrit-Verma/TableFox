from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit


def _env_file() -> Path | None:
    configured = os.getenv("DBMAP_ENV_FILE")
    if configured:
        return Path(configured).expanduser().resolve()

    search_roots = (Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents)
    for root in search_roots:
        candidate = root / ".env"
        if candidate.is_file():
            return candidate
    return None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


def _env_path(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else None


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str
    cache_dir: Path
    statement_timeout_ms: int
    max_query_rows: int
    api_host: str
    api_port: int
    max_explain_cost: float = 100000.0
    max_explain_rows: int = 100000
    allow_sensitive_data: bool = False
    allowed_schemas: tuple[str, ...] = ()
    restricted_schemas: tuple[str, ...] = ()
    enable_usage_telemetry: bool = False
    context_file: Path | None = None
    baseline_file: Path | None = None
    audit_dir: Path = Path(".logs/audit")
    audit_retention_days: int = 30
    auth_required: bool = False
    auth_file: Path | None = None
    mcp_actor: str = "local-mcp"

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv

            env_file = _env_file()
            if env_file:
                load_dotenv(env_file, override=True)
        except ImportError:
            pass

        return cls(
            database_url=os.getenv("DATABASE_URL"),
            host=os.getenv("PGHOST", "localhost"),
            port=int(os.getenv("PGPORT", "5432")),
            database=os.getenv("PGDATABASE", "postgres"),
            user=os.getenv("PGUSER", "postgres"),
            password=os.getenv("PGPASSWORD", ""),
            sslmode=os.getenv("PGSSLMODE", "prefer"),
            cache_dir=Path(os.getenv("DBMAP_CACHE_DIR", ".dbmap-cache")),
            statement_timeout_ms=int(os.getenv("DBMAP_STATEMENT_TIMEOUT_MS", "5000")),
            max_query_rows=int(os.getenv("DBMAP_MAX_QUERY_ROWS", "200")),
            api_host=os.getenv("DBMAP_API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("DBMAP_API_PORT", "8000")),
            max_explain_cost=float(os.getenv("DBMAP_MAX_EXPLAIN_COST", "100000")),
            max_explain_rows=int(os.getenv("DBMAP_MAX_EXPLAIN_ROWS", "100000")),
            allow_sensitive_data=_env_bool("DBMAP_ALLOW_SENSITIVE_DATA", False),
            allowed_schemas=_env_list("DBMAP_ALLOWED_SCHEMAS"),
            restricted_schemas=_env_list("DBMAP_RESTRICTED_SCHEMAS"),
            enable_usage_telemetry=_env_bool("DBMAP_ENABLE_USAGE_TELEMETRY", False),
            context_file=_env_path("DBMAP_CONTEXT_FILE"),
            baseline_file=_env_path("DBMAP_BASELINE_FILE"),
            audit_dir=Path(os.getenv("DBMAP_AUDIT_DIR", ".logs/audit")),
            audit_retention_days=max(1, int(os.getenv("DBMAP_AUDIT_RETENTION_DAYS", "30"))),
            auth_required=_env_bool("DBMAP_AUTH_REQUIRED", False),
            auth_file=_env_path("DBMAP_AUTH_FILE"),
            mcp_actor=os.getenv("DBMAP_MCP_ACTOR", "local-mcp").strip() or "local-mcp",
        )

    def connection_kwargs(self) -> dict[str, object]:
        if self.database_url:
            return {"conninfo": self.database_url}
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
        }

    def safe_database_label(self) -> str:
        if self.database_url:
            parsed = urlsplit(self.database_url)
            if parsed.hostname:
                port = f":{parsed.port}" if parsed.port else ""
                database = unquote(parsed.path.lstrip("/")) or "postgres"
                return f"{parsed.hostname}{port}/{database}"
            return "database-url"
        return f"{self.host}:{self.port}/{self.database}"

    def cache_identity(self) -> str:
        if self.database_url:
            parsed = urlsplit(self.database_url)
            return f"{self.safe_database_label()}|{unquote(parsed.username or '')}"
        return f"{self.safe_database_label()}|{self.user}"

    def require_local_api(self) -> None:
        if self.api_host.lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "DBMAP_API_HOST must be a loopback address because this local-only API "
                "does not provide authentication."
            )
