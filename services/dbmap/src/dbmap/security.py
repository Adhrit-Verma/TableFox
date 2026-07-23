from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any


ROLE_SCOPES = {
    "viewer": {"metadata"},
    "analyst": {"metadata", "explain", "workflow"},
    "data_reader": {"metadata", "explain", "workflow", "query"},
    "admin": {"metadata", "explain", "workflow", "query", "approve"},
}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str

    def can(self, scope: str) -> bool:
        return scope in ROLE_SCOPES.get(self.role, set())


class ApiKeyAuth:
    def __init__(self, path: Path | None, required: bool) -> None:
        self.path = path
        self.required = required

    def authenticate(self, authorization: str | None) -> Principal:
        if not self.required:
            return Principal("local-user", "admin")
        if not authorization or not authorization.startswith("Bearer "):
            raise PermissionError("A Bearer API key is required.")

        digest = hashlib.sha256(authorization[7:].encode("utf-8")).hexdigest()
        for user in self._users():
            if hmac.compare_digest(str(user.get("key_sha256", "")), digest):
                role = str(user.get("role", ""))
                if role not in ROLE_SCOPES:
                    break
                return Principal(str(user.get("name") or "unknown"), role)
        raise PermissionError("The API key is invalid.")

    def _users(self) -> list[dict[str, Any]]:
        if not self.path or not self.path.is_file():
            raise PermissionError("API authentication is not configured.")
        if self.path.stat().st_size > 64_000:
            raise PermissionError("The API authentication file is too large.")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        users = payload.get("users", []) if isinstance(payload, dict) else []
        if not isinstance(users, list):
            raise PermissionError("The API authentication file is invalid.")
        return [user for user in users if isinstance(user, dict)]


def schema_allowed(
    schema: str | None,
    allowed_schemas: tuple[str, ...],
    restricted_schemas: tuple[str, ...],
) -> bool:
    if not schema:
        return False
    if schema in restricted_schemas:
        return False
    return not allowed_schemas or schema in allowed_schemas


def filter_metadata_schemas(
    metadata: dict[str, Any],
    allowed_schemas: tuple[str, ...],
    restricted_schemas: tuple[str, ...],
) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, rows in metadata.items():
        if not isinstance(rows, list):
            filtered[key] = rows
            continue
        filtered[key] = [
            row
            for row in rows
            if schema_allowed(row.get("schema"), allowed_schemas, restricted_schemas)
            and (
                not (row.get("target_schema") or row.get("foreign_schema"))
                or schema_allowed(
                    row.get("target_schema") or row.get("foreign_schema"),
                    allowed_schemas,
                    restricted_schemas,
                )
            )
        ]
    return filtered
