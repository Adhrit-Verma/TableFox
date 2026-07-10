from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from .config import Settings
from .graph import GraphEngine
from .models import GraphSnapshot
from .readonly import apply_limit


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
            conn.execute("set transaction read only")
            conn.execute(f"set statement_timeout = {int(self.settings.statement_timeout_ms)}")
            row = conn.execute(
                "select current_database(), current_user, version(), pg_is_in_recovery()"
            ).fetchone()
            return {
                "ok": True,
                "database": row[0],
                "user": row[1],
                "version": row[2],
                "read_replica": row[3],
                "read_only_default": True,
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
            conn.execute("set transaction read only")
            conn.execute(f"set statement_timeout = {int(self.settings.statement_timeout_ms)}")
            return {
                "relations": list(conn.execute(RELATIONS_SQL)),
                "columns": list(conn.execute(COLUMNS_SQL)),
                "constraints": list(conn.execute(CONSTRAINTS_SQL)),
                "indexes": list(conn.execute(INDEXES_SQL)),
            }

    def readonly_query(self, sql: str, limit: int | None = None) -> dict[str, Any]:
        import psycopg
        from psycopg.rows import dict_row

        row_limit = min(limit or self.settings.max_query_rows, self.settings.max_query_rows)
        guarded_sql = apply_limit(sql, row_limit)
        with psycopg.connect(**self.settings.connection_kwargs(), row_factory=dict_row) as conn:
            conn.execute("set transaction read only")
            conn.execute(f"set statement_timeout = {int(self.settings.statement_timeout_ms)}")
            rows = list(conn.execute(guarded_sql))
            return {
                "sql": guarded_sql,
                "row_count": len(rows),
                "limit": row_limit,
                "rows": rows,
            }

    def _cache_path(self) -> Path:
        identity = self.settings.database_url or self.settings.safe_database_label()
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
        path.write_text(json.dumps(snapshot.to_dict(), indent=2, default=str), encoding="utf-8")
