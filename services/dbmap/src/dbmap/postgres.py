from __future__ import annotations

import json
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
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

    def fetch_metadata(self) -> dict[str, Any]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(**self.settings.connection_kwargs(), row_factory=dict_row) as conn:
            self._configure_readonly_transaction(conn)
            metadata: dict[str, Any] = {
                "relations": list(conn.execute(RELATIONS_SQL)),
                "columns": list(conn.execute(COLUMNS_SQL)),
                "constraints": list(conn.execute(CONSTRAINTS_SQL)),
                "indexes": list(conn.execute(INDEXES_SQL)),
                "dependencies": list(conn.execute(DEPENDENCIES_SQL)),
                "usage": [],
                "usage_status": "disabled",
            }
            if self.settings.enable_usage_telemetry:
                try:
                    with conn.transaction():
                        metadata["usage"] = list(conn.execute(USAGE_SQL))
                    metadata["usage_status"] = "available"
                except Exception:
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
        import psycopg
        from psycopg.rows import dict_row

        requested_limit = self.settings.max_query_rows if limit is None else limit
        row_limit = max(1, min(requested_limit, self.settings.max_query_rows))
        guarded_sql = apply_limit(sql, row_limit)
        plan = self.explain_query(sql)
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
                "rows": [],
            }
        with psycopg.connect(**self.settings.connection_kwargs(), row_factory=dict_row) as conn:
            self._configure_readonly_transaction(conn)
            cursor = conn.execute(guarded_sql)
            columns = [column.name for column in cursor.description or []]
            sensitive_columns = classify_sensitive_columns(
                columns,
                self._context_classifications(),
            )
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
                "plan": plan,
                "join_validation": join_validation,
                "row_count": len(rows),
                "limit": row_limit,
                "rows": rows,
            }

    def explain_query(self, sql: str, include_plan: bool = False) -> dict[str, Any]:
        import psycopg

        statement = validate_readonly_sql(sql)
        explain_sql = (
            "EXPLAIN (FORMAT JSON, ANALYZE FALSE, BUFFERS FALSE, VERBOSE TRUE) "
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
