from __future__ import annotations

import atexit
import hashlib
import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock, Thread
from typing import Any

from .config import Settings
from .context import apply_context
from .graph import GraphEngine
from .models import GraphSnapshot
from .query_policy import assess_query_plan, classify_sensitive_columns
from .readonly import apply_limit, validate_readonly_sql
from .security import filter_metadata_schemas, schema_allowed

RELATIONS_SQL = """
select
  n.nspname as schema,
  c.relname as name,
  case c.relkind
    when 'r' then 'table'
    when 'p' then 'table'
    when 'v' then 'view'
    when 'm' then 'materialized_view'
    else 'relation'
  end as kind,
  obj_description(c.oid, 'pg_class') as comment,
  c.reltuples::bigint as row_estimate
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r', 'p', 'v', 'm')
  and n.nspname not in ('pg_catalog', 'information_schema')
order by n.nspname, c.relname
"""

COLUMNS_SQL = """
select
  table_schema as schema,
  table_name as table,
  column_name as name,
  ordinal_position,
  data_type,
  is_nullable,
  column_default as default,
  col_description((quote_ident(table_schema) || '.' || quote_ident(table_name))::regclass::oid, ordinal_position) as comment
from information_schema.columns
where table_schema not in ('pg_catalog', 'information_schema')
order by table_schema, table_name, ordinal_position
"""

CONSTRAINTS_SQL = """
select
  n.nspname as schema,
  rel.relname as table,
  con.conname as name,
  case con.contype
    when 'p' then 'PRIMARY KEY'
    when 'f' then 'FOREIGN KEY'
    when 'u' then 'UNIQUE'
    when 'c' then 'CHECK'
    when 'x' then 'EXCLUDE'
  end as type,
  array(
    select att.attname
    from unnest(con.conkey) with ordinality as key(attnum, position)
    join pg_attribute att on att.attrelid = con.conrelid and att.attnum = key.attnum
    order by key.position
  ) as columns,
  foreign_n.nspname as foreign_schema,
  foreign_rel.relname as foreign_table,
  array(
    select att.attname
    from unnest(con.confkey) with ordinality as key(attnum, position)
    join pg_attribute att on att.attrelid = con.confrelid and att.attnum = key.attnum
    order by key.position
  ) as foreign_columns
from pg_constraint con
join pg_class rel on rel.oid = con.conrelid
join pg_namespace n on n.oid = rel.relnamespace
left join pg_class foreign_rel on foreign_rel.oid = con.confrelid
left join pg_namespace foreign_n on foreign_n.oid = foreign_rel.relnamespace
where con.contype in ('p', 'f', 'u', 'c', 'x')
  and n.nspname not in ('pg_catalog', 'information_schema')
order by n.nspname, rel.relname, con.conname
"""

INDEXES_SQL = """
select
  schemaname as schema,
  tablename as table,
  indexname as name,
  indexdef as definition,
  ix.indisunique as is_unique,
  ix.indisprimary as is_primary,
  array_remove(array_agg(a.attname order by array_position(ix.indkey::int[], a.attnum)), null) as columns
from pg_indexes i
join pg_class t on t.relname = i.tablename
join pg_namespace n on n.oid = t.relnamespace and n.nspname = i.schemaname
join pg_class idx on idx.relname = i.indexname and idx.relnamespace = n.oid
join pg_index ix on ix.indexrelid = idx.oid
left join pg_attribute a on a.attrelid = t.oid and a.attnum = any(ix.indkey)
where schemaname not in ('pg_catalog', 'information_schema')
group by schemaname, tablename, indexname, indexdef, ix.indisunique, ix.indisprimary
order by schemaname, tablename, indexname
"""

DEPENDENCIES_SQL = """
select distinct
  source_ns.nspname as schema,
  source.relname as name,
  target_ns.nspname as target_schema,
  target.relname as target_name
from pg_rewrite rewrite
join pg_class source on source.oid = rewrite.ev_class
join pg_namespace source_ns on source_ns.oid = source.relnamespace
join pg_depend dependency on dependency.objid = rewrite.oid
join pg_class target on target.oid = dependency.refobjid
join pg_namespace target_ns on target_ns.oid = target.relnamespace
where source.relkind in ('v', 'm')
  and target.relkind in ('r', 'p', 'v', 'm')
  and source.oid <> target.oid
  and source_ns.nspname not in ('pg_catalog', 'information_schema')
  and target_ns.nspname not in ('pg_catalog', 'information_schema')
order by source_ns.nspname, source.relname, target_ns.nspname, target.relname
"""

USAGE_SQL = """
select
  stats.schemaname as schema,
  stats.relname as name,
  stats.seq_scan::bigint as sequential_scans,
  stats.idx_scan::bigint as index_scans,
  stats.n_live_tup::bigint as live_rows,
  greatest(stats.last_analyze, stats.last_autoanalyze) as last_analyze,
  database_stats.stats_reset
from pg_stat_user_tables stats
left join pg_stat_database database_stats on database_stats.datname = current_database()
order by stats.schemaname, stats.relname
"""


class PostgresIntrospector:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self._pool: Any | None = None
        self._pool_lock = Lock()
        self._prewarming = False

    def _connection(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    from psycopg_pool import ConnectionPool

                    minimum = min(
                        self.settings.pool_min_size,
                        self.settings.pool_max_size,
                    )
                    kwargs = self.settings.connection_kwargs()
                    # The pool passes conninfo positionally; leaving it in kwargs makes every
                    # DATABASE_URL connection fail with "multiple values for 'conninfo'".
                    self._pool = ConnectionPool(
                        str(kwargs.pop("conninfo", "")),
                        kwargs=kwargs,
                        min_size=minimum,
                        max_size=self.settings.pool_max_size,
                        open=False,
                        timeout=max(10.0, self.settings.statement_timeout_ms / 1000),
                    )
                    self._pool.open(wait=True)
                    atexit.register(self.close)
        return self._pool.connection()

    def prewarm(self) -> None:
        """Open the pool in the background so the first query skips the handshake.

        Called when a task context is requested, because a guarded read almost always
        follows. Failures are ignored here; the real request reports them.
        """
        with self._pool_lock:
            if self._pool is not None or self._prewarming:
                return
            self._prewarming = True

        def open_pool() -> None:
            try:
                with self._connection():
                    pass
            # Best-effort warm-up; the first real query reports connection errors.
            except Exception:  # noqa: BLE001, S110  # pragma: no cover - depends on external database
                pass

        Thread(target=open_pool, name="dbmap-prewarm", daemon=True).start()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    def connectivity_check(self) -> dict[str, Any]:
        with self._connection() as conn:
            self._configure_readonly_transaction(conn)
            row = conn.execute(
                "select current_database(), current_user, version(), pg_is_in_recovery(), "
                "current_setting('default_transaction_read_only')::boolean"
            ).fetchone()
            return {
                "ok": True,
                "database": row[0],
                "user": row[1],
                "version": row[2],
                "read_replica": row[3],
                "read_only_default": row[4],
                "session_enforced_read_only": True,
            }

    def snapshot(self, use_cache: bool = True, refresh: bool = False) -> GraphSnapshot:
        cache_path = self._cache_path()
        if use_cache and not refresh and cache_path.exists():
            return self._load_cache(cache_path)

        metadata = self.fetch_metadata()
        snapshot = GraphEngine(self.settings.safe_database_label()).build(metadata)
        if use_cache:
            self._write_cache(cache_path, snapshot)
        return snapshot

    def fetch_metadata(self) -> dict[str, Any]:
        from psycopg.rows import dict_row

        with self._connection() as conn:
            self._configure_readonly_transaction(conn)
            with conn.cursor(row_factory=dict_row) as cursor:
                metadata: dict[str, Any] = {
                    "relations": list(cursor.execute(RELATIONS_SQL)),
                    "columns": list(cursor.execute(COLUMNS_SQL)),
                    "constraints": list(cursor.execute(CONSTRAINTS_SQL)),
                    "indexes": list(cursor.execute(INDEXES_SQL)),
                    "dependencies": list(cursor.execute(DEPENDENCIES_SQL)),
                    "usage": [],
                    "usage_status": "disabled",
                }
                if self.settings.enable_usage_telemetry:
                    try:
                        with conn.transaction():
                            metadata["usage"] = list(cursor.execute(USAGE_SQL))
                        metadata["usage_status"] = "available"
                    except Exception:  # noqa: BLE001 - optional telemetry must not fail introspection
                        metadata["usage_status"] = "unavailable"
            return filter_metadata_schemas(
                metadata,
                self.settings.allowed_schemas,
                self.settings.restricted_schemas,
            )

    def readonly_query(
        self,
        sql: str,
        limit: int | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            self._configure_readonly_transaction(conn)
            return self._readonly_query_on_connection(conn, sql, limit, approved)

    def readonly_batch(
        self,
        queries: list[dict[str, Any]],
        max_rows_each: int | None = None,
        max_bytes: int = 32_768,
        approved: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= len(queries) <= 5:
            raise ValueError("A read-only batch requires between one and five queries.")
        prepared = []
        names: set[str] = set()
        for item in queries:
            if not isinstance(item, dict):
                raise ValueError("Each batch query must be an object.")  # noqa: TRY004 - surfaced as a client error
            name = str(item.get("name", "")).strip()
            sql = str(item.get("sql", "")).strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
                raise ValueError("Each query name must be a short identifier.")
            if name in names:
                raise ValueError("Query names must be unique within a batch.")
            names.add(name)
            validate_readonly_sql(sql)
            requested_limit = item.get("limit") or max_rows_each
            prepared.append((name, sql, requested_limit))

        classifications = self._context_classifications()
        results = []
        with self._connection() as conn:
            self._configure_readonly_transaction(conn)
            for name, sql, limit in prepared:
                full = self._readonly_query_on_connection(
                    conn,
                    sql,
                    limit,
                    approved,
                    classifications,
                )
                columns = full.get("columns", [])
                result = {
                    "name": name,
                    "columns": columns,
                    "rows": [[row.get(column) for column in columns] for row in full["rows"]],
                    "row_count": full["row_count"],
                }
                if full["blocked"]:
                    result.update({"blocked": True, "reason": full.get("reason")})
                review = []
                if not full.get("plan", {}).get("within_policy", False):
                    review.append("cost")
                if not full.get("join_validation", {}).get("verified", False):
                    review.append("join")
                if full.get("sensitive_columns"):
                    review.append("sensitive")
                if review:
                    result["review"] = review
                results.append(result)
        return self._bound_batch(results, max(2_048, min(max_bytes, 1_000_000)))

    def explain_query(self, sql: str, include_plan: bool = False) -> dict[str, Any]:
        with self._connection() as conn:
            self._configure_readonly_transaction(conn)
            return self._explain_query_on_connection(conn, sql, include_plan)

    def _explain_query_on_connection(
        self,
        conn: Any,
        sql: str,
        include_plan: bool = False,
    ) -> dict[str, Any]:
        statement = validate_readonly_sql(sql)
        explain_sql = (
            "EXPLAIN (FORMAT JSON, ANALYZE FALSE, BUFFERS FALSE, VERBOSE TRUE) "
            f"{statement}"
        )
        row = conn.execute(explain_sql).fetchone()
        if not row:
            raise RuntimeError("PostgreSQL returned no EXPLAIN plan.")
        result = assess_query_plan(
            row[0],
            max_total_cost=self.settings.max_explain_cost,
            max_plan_rows=self.settings.max_explain_rows,
            include_plan=include_plan,
        )
        result["sql"] = statement
        blocked_relations = []
        for relation in result["summary"]["relations"]:
            schema = relation.split(".", 1)[0] if "." in relation else None
            if not schema_allowed(
                schema,
                self.settings.allowed_schemas,
                self.settings.restricted_schemas,
            ):
                blocked_relations.append(relation)
        if blocked_relations:
            result["blocking_reasons"].append(
                {"code": "schema_not_allowed", "relations": sorted(blocked_relations)}
            )
            result["within_policy"] = False
            result["approval_required"] = True
        return result

    def _readonly_query_on_connection(
        self,
        conn: Any,
        sql: str,
        limit: int | None,
        approved: bool,
        classifications: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from psycopg.rows import dict_row

        requested_limit = self.settings.max_query_rows if limit is None else int(limit)
        row_limit = max(1, min(requested_limit, self.settings.max_query_rows))
        guarded_sql = apply_limit(sql, row_limit)
        plan = self._explain_query_on_connection(conn, sql)
        join_validation = self._validate_plan_relations(plan)
        restricted = any(
            reason.get("code") == "schema_not_allowed"
            for reason in plan.get("blocking_reasons", [])
        )
        needs_approval = not plan["within_policy"] or not join_validation["verified"]
        if restricted or (needs_approval and not approved):
            return {
                "sql": guarded_sql,
                "blocked": True,
                "reason": (
                    "Query references a schema forbidden by policy."
                    if restricted
                    else "Query requires approval because it is outside the low-risk policy."
                ),
                "approval_required": not restricted,
                "plan": plan,
                "join_validation": join_validation,
                "row_count": 0,
                "limit": row_limit,
                "columns": [],
                "rows": [],
            }
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(guarded_sql)
            columns = [column.name for column in cursor.description or []]
            sensitive_columns = classify_sensitive_columns(
                columns,
                classifications if classifications is not None else self._context_classifications(),
            )
            if sensitive_columns and not self.settings.allow_sensitive_data:
                return {
                    "sql": guarded_sql,
                    "blocked": True,
                    "reason": "Result columns matched the sensitive-data policy.",
                    "sensitive_columns": sensitive_columns,
                    "plan": plan,
                    "join_validation": join_validation,
                    "row_count": 0,
                    "limit": row_limit,
                    "columns": columns,
                    "rows": [],
                }
            rows = list(cursor)
        return {
            "sql": guarded_sql,
            "blocked": False,
            "sensitive_columns": sensitive_columns,
            "plan": plan,
            "join_validation": join_validation,
            "row_count": len(rows),
            "limit": row_limit,
            "columns": columns,
            "rows": rows,
        }

    @staticmethod
    def _bound_batch(results: list[dict[str, Any]], max_bytes: int) -> dict[str, Any]:
        response = {"results": results}
        while len(json.dumps(response, default=str, separators=(",", ":")).encode()) > max_bytes - 32:
            result = next((item for item in reversed(results) if item["rows"]), None)
            if result is None:
                raise ValueError("The batch metadata exceeds the response byte limit.")
            result["rows"].pop()
            result["truncated"] = True
            result["returned_rows"] = len(result["rows"])
            response["truncated"] = True
        response["bytes"] = len(
            json.dumps(response, default=str, separators=(",", ":")).encode()
        )
        return response

    def _validate_plan_relations(self, plan: dict[str, Any]) -> dict[str, Any]:
        relations = set(plan.get("summary", {}).get("relations", []))
        if len(relations) < 2:
            return {"verified": True, "relations": sorted(relations), "edges": []}
        snapshot = self.snapshot()
        ids_by_label = {
            node.label: node.id
            for node in snapshot.nodes
            if node.kind in {"table", "view", "materialized_view"}
        }
        missing = sorted(relations - ids_by_label.keys())
        if missing:
            return {
                "verified": False,
                "relations": sorted(relations),
                "missing_from_graph": missing,
                "edges": [],
            }
        return GraphEngine.validate_relation_set(
            snapshot,
            {ids_by_label[relation] for relation in relations},
        )

    def _context_classifications(self) -> dict[str, str]:
        if not self.settings.context_file:
            return {}
        snapshot = apply_context(self.snapshot(), self.settings.context_file)
        return {
            str(node.name).lower(): str(node.metadata["context"]["classification"])
            for node in snapshot.nodes
            if node.kind == "column"
            and node.name
            and node.metadata.get("context", {}).get("classification")
            not in {None, "", "unclassified"}
        }

    def _configure_readonly_transaction(self, conn: Any) -> None:
        timeout = max(1, int(self.settings.statement_timeout_ms))
        lock_timeout = min(timeout, 1000)
        conn.execute("set transaction read only")
        conn.execute(f"set statement_timeout = {timeout}")
        conn.execute(f"set lock_timeout = {lock_timeout}")

    def _cache_path(self) -> Path:
        identity = self.settings.cache_identity()
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return self.settings.cache_dir / f"{key}.snapshot.json"

    @staticmethod
    def _load_cache(path: Path) -> GraphSnapshot:
        from .models import GraphEdge, GraphNode

        payload = json.loads(path.read_text(encoding="utf-8"))
        return GraphSnapshot(
            database=payload["database"],
            generated_at=payload["generated_at"],
            summary=payload["summary"],
            nodes=[GraphNode(**node) for node in payload["nodes"]],
            edges=[GraphEdge(**edge) for edge in payload["edges"]],
        )

    @staticmethod
    def _write_cache(path: Path, snapshot: GraphSnapshot) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(snapshot.to_dict(), handle, indent=2, default=str)
            temporary_path.replace(path)
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
