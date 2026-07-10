from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


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

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv

            load_dotenv()
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
            return "database-url"
        return f"{self.host}:{self.port}/{self.database}"
