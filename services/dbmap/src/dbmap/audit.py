from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any


class AuditLog:
    """Append-only JSONL audit sink. Callers pass only already-sanitized details."""

    def __init__(self, directory: Path, retention_days: int = 30) -> None:
        self.directory = directory
        self.retention_days = max(1, retention_days)
        self._lock = Lock()
        self._cleaned = False

    def record(
        self,
        actor: str,
        action: str,
        *,
        outcome: str = "success",
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        event = {
            "timestamp": now.isoformat(),
            "actor": actor[:200],
            "action": action[:100],
            "outcome": outcome,
            "target": target[:500] if target else None,
            "details": details or {},
        }
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            if not self._cleaned:
                self._cleanup(now)
                self._cleaned = True
            path = self.directory / f"audit-{now.date().isoformat()}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, default=str, separators=(",", ":")) + "\n")

    def _cleanup(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=self.retention_days)).date().isoformat()
        for path in self.directory.glob("audit-*.jsonl"):
            date_part = path.stem.removeprefix("audit-")
            if date_part < cutoff:
                path.unlink(missing_ok=True)
