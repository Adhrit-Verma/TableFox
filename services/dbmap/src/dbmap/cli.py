from __future__ import annotations

import json
import os

from .config import Settings
from .postgres import PostgresIntrospector


def print_snapshot() -> None:
    snapshot = PostgresIntrospector().snapshot(refresh=True)
    print(json.dumps(snapshot.to_dict(), indent=2, default=str))


def run_api() -> None:
    import uvicorn

    settings = Settings.from_env()
    settings.require_local_api()
    reload_enabled = os.getenv("DBMAP_API_RELOAD", "0").lower() in {"1", "true", "yes"}
    uvicorn.run("dbmap.api:app", host=settings.api_host, port=settings.api_port, reload=reload_enabled)
