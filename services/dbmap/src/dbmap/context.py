from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .diff import schema_fingerprint
from .models import GraphSnapshot


MAX_CONTEXT_BYTES = 1_000_000
MAX_CONTEXT_OBJECTS = 10_000


def apply_context(snapshot: GraphSnapshot, path: Path | None) -> GraphSnapshot:
    if not path:
        return snapshot
    manifest = _load_manifest(path)
    expected_database = manifest.get("database")
    if expected_database != snapshot.database:
        raise ValueError("Context database identity does not match the connected database.")

    objects = manifest.get("objects", {})
    if not isinstance(objects, dict) or len(objects) > MAX_CONTEXT_OBJECTS:
        raise ValueError("Context objects must be a bounded JSON object.")
    fingerprint_matches = manifest.get("schema_fingerprint") == schema_fingerprint(snapshot)
    nodes = []
    for node in snapshot.nodes:
        entry = objects.get(node.id)
        if not isinstance(entry, dict):
            nodes.append(node)
            continue
        context = _validate_entry(entry, fingerprint_matches)
        nodes.append(replace(node, metadata={**node.metadata, "context": context}))
    return replace(snapshot, nodes=nodes)


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Context file does not exist: {path}")
    if path.stat().st_size > MAX_CONTEXT_BYTES:
        raise ValueError("Context file exceeds the 1 MB safety limit.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Context file must contain a JSON object.")
    return payload


def _validate_entry(entry: dict[str, Any], fingerprint_matches: bool) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key in ("description", "owner", "updated_at"):
        if entry.get(key) is not None:
            value[key] = str(entry[key])[:2000]
    value["source_of_truth"] = entry.get("source_of_truth") is True
    value["documents"] = _bounded_records(
        entry.get("documents", []),
        100,
        required=("source", "updated_at"),
    )
    value["consumers"] = _bounded_records(
        entry.get("consumers", []),
        100,
        required=("source", "updated_at"),
    )
    value["saved_queries"] = _bounded_records(
        entry.get("saved_queries", []),
        100,
        required=("source", "updated_at"),
    )
    value["classification"] = str(entry.get("classification", "unclassified"))[:100]
    value["code_links"] = (
        _bounded_records(
            entry.get("code_links", []),
            100,
            required=("kind", "path", "revision"),
        )
        if fingerprint_matches
        else []
    )
    value["code_links_status"] = "matched" if fingerprint_matches else "schema_mismatch"
    return value


def _bounded_records(
    value: Any,
    limit: int,
    required: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    records = []
    for item in value[:limit]:
        if isinstance(item, dict):
            record = {
                str(key)[:100]: str(item_value)[:2000]
                for key, item_value in item.items()
            }
            updated_at = record.get("updated_at")
            url = record.get("url")
            if updated_at and not _valid_timestamp(updated_at):
                continue
            if url and urlsplit(url).scheme not in {"http", "https"}:
                continue
            if all(record.get(key) for key in required):
                records.append(record)
    return records


def _valid_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False
