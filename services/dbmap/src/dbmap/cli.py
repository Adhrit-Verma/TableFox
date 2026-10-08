from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from .config import Settings
from .service import build_service


def print_snapshot() -> None:
    snapshot = build_service().graph_snapshot(refresh=True, actor="cli")
    print(json.dumps(snapshot.to_dict(), indent=2, default=str))


def print_identity() -> None:
    print(json.dumps(build_service().snapshot_identity(actor="cli"), indent=2))


def create_api_key() -> None:
    import hashlib

    key = secrets.token_urlsafe(32)
    print(f"API key (shown once): {key}")
    print(f"SHA-256 for auth file: {hashlib.sha256(key.encode('utf-8')).hexdigest()}")


def scaffold_context() -> None:
    """Emit a context manifest skeleton for relations with no description.

    Lexical retrieval only sees schema text, so a table whose meaning lives in its row
    values (an audit log keyed by entity_type, for example) cannot be found by name alone.
    Filling in these descriptions is the supported way to make such tables discoverable.
    """
    import argparse

    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Scaffold an approved-context manifest.")
    parser.add_argument("--output", type=Path, default=settings.context_file)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include relations that already have a database comment.",
    )
    args = parser.parse_args()
    if not args.output:
        raise ValueError("Set DBMAP_CONTEXT_FILE or pass --output.")

    service = build_service(settings)
    snapshot = service.graph_snapshot(refresh=True, actor="cli")
    identity = service.snapshot_identity(actor="cli")
    existing: dict[str, Any] = {}
    if args.output.is_file():
        try:
            existing = json.loads(args.output.read_text(encoding="utf-8")).get("objects", {})
        except (OSError, ValueError):
            existing = {}

    objects = dict(existing)
    added = 0
    for node in snapshot.nodes:
        if node.kind not in {"table", "view", "materialized_view"} or node.id in objects:
            continue
        if node.metadata.get("comment") and not args.all:
            continue
        objects[node.id] = {
            "description": "",
            "owner": "",
            "updated_at": "",
            "source_of_truth": False,
            "classification": "unclassified",
        }
        added += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "database": identity["database"],
                "schema_fingerprint": identity["schema_fingerprint"],
                "objects": objects,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output} ({added} new entries, {len(objects)} total).")
    print("Fill in each description, then agents can find these tables by meaning.")


def save_baseline() -> None:
    import argparse

    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Save the current schema as a baseline.")
    parser.add_argument("--output", type=Path, default=settings.baseline_file)
    args = parser.parse_args()
    if not args.output:
        raise ValueError("Set DBMAP_BASELINE_FILE or pass --output.")
    snapshot = build_service(settings).graph_snapshot(refresh=True, actor="cli")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot.to_dict(), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"Saved baseline: {args.output}")


def review_schema() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Compare a schema baseline to PostgreSQL.")
    parser.add_argument("--baseline", type=Path, help="Baseline snapshot JSON path.")
    parser.add_argument("--output", type=Path, help="Optional report JSON path.")
    parser.add_argument("--fail-on-change", action="store_true")
    args = parser.parse_args()
    report = build_service().schema_changes(
        baseline=args.baseline.resolve() if args.baseline else None,
        actor="ci",
    )
    rendered = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["changed"] and args.fail_on_change:
        sys.exit(2)


def run_api() -> None:
    import uvicorn

    settings = Settings.from_env()
    settings.require_local_api()
    reload_enabled = os.getenv("DBMAP_API_RELOAD", "0").lower() in {"1", "true", "yes"}
    uvicorn.run("dbmap.api:app", host=settings.api_host, port=settings.api_port, reload=reload_enabled)
