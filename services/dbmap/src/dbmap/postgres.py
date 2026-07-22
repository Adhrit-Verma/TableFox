from __future__ import annotations

import json
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .config import Settings
from .graph import GraphEngine
from .models import GraphSnapshot
from .query_policy import assess_query_plan, classify_sensitive_columns
from .readonly import apply_limit, validate_readonly_sql


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


class PostgresIntrospector:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def connectivity_check(self) -> dict[str, Any]:
        import psycopg

        with psycopg.connect(**self.settings.connection_kwargs()) as conn:
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

    def fetch_metadata(self) -> dict[str, list[dict]]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(**self.settings.connection_kwargs(), row_factory=dict_row) as conn:
            self._configure_readonly_transaction(conn)
            return {
                "relations": list(conn.execute(RELATIONS_SQL)),
                "columns": list(conn.execute(COLUMNS_SQL)),
                "constraints": list(conn.execute(CONSTRAINTS_SQL)),
                "indexes": list(conn.execute(INDEXES_SQL)),
            }

    def readonly_query(self, sql: str, limit: int | None = None) -> dict[str, Any]:
        import psycopg
        from psycopg.rows import dict_row

        requested_limit = self.settings.max_query_rows if limit is None else limit
        row_limit = max(1, min(requested_limit, self.settings.max_query_rows))
        guarded_sql = apply_limit(sql, row_limit)
        with psycopg.connect(**self.settings.connection_kwargs(), row_factory=dict_row) as conn:
            self._configure_readonly_transaction(conn)
            cursor = conn.execute(guarded_sql)
            columns = [column.name for column in cursor.description or []]
            sensitive_columns = classify_sensitive_columns(columns)
            if sensitive_columns and not self.settings.allow_sensitive_data:
                cursor.close()
                return {
                    "sql": guarded_sql,
                    "blocked": True,
                    "reason": "Result columns matched the sensitive-data policy.",
                    "sensitive_columns": sensitive_columns,
                    "row_count": 0,
                    "limit": row_limit,
                    "rows": [],
                }
            rows = list(cursor)
            return {
                "sql": guarded_sql,
                "blocked": False,
                "sensitive_columns": sensitive_columns,
                "row_count": len(rows),
                "limit": row_limit,
                "rows": rows,
            }

    def explain_query(self, sql: str, include_plan: bool = False) -> dict[str, Any]:
        import psycopg

        statement = validate_readonly_sql(sql)
        explain_sql = (
            "EXPLAIN (FORMAT JSON, ANALYZE FALSE, BUFFERS FALSE, VERBOSE FALSE) "
            f"{statement}"
        )
        with psycopg.connect(**self.settings.connection_kwargs()) as conn:
            self._configure_readonly_transaction(conn)
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
        return result

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
