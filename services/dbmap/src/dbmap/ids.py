from __future__ import annotations

import re


_UNSAFE = re.compile(r"[^a-zA-Z0-9_.-]+")


def normalize_part(value: object) -> str:
    text = str(value or "").strip().lower()
    text = _UNSAFE.sub("_", text)
    return text.strip("_") or "unknown"


def node_id(kind: str, *parts: object) -> str:
    normalized = ".".join(normalize_part(part) for part in parts if part is not None)
    return f"{normalize_part(kind)}:{normalized}"


def edge_id(kind: str, source: str, target: str) -> str:
    return f"{normalize_part(kind)}:{normalize_part(source)}->{normalize_part(target)}"
