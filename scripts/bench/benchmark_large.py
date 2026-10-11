"""Extended TableFox benchmark for the large local test database (scripts/bench/*.sql).

Measures what scripts/benchmark_corpus.py does not:

* retrieval quality: schema-qualified recall@6 and recall over the whole pack, plus the
  1-based rank of every needed relation in the database_task_context response;
* answer accuracy: rows returned by database_readonly_batch versus the same SQL run directly
  on a fresh read-only connection (values normalised; row order ignored unless the SQL ends
  with a top-level ORDER BY);
* latency split: task_context versus readonly_batch, warm median / p95 / p99;
* payload: bytes and estimated tokens (ceil(chars / 4)) per call;
* policy: which ground-truth queries the EXPLAIN cost/row policy refuses;
* cold start: first calls in a fresh process with and without the snapshot cache;
* the safety suite (write refusal and sensitive-column blocking);
* blind authoring: runs the attempts in docs/benchmarks/large-db/blind-attempts.json.

Everything talks to TableFox through the real stdio MCP server (python -m dbmap.mcp_server).
Always point it at the benchmark database, never at the repo .env:

    $env:DBMAP_ENV_FILE = "C:\\...\\Database Agent\\.env.bench"
    .venv\\Scripts\\python scripts\\bench\\benchmark_large.py run --repeat 3 --window 0 --label default
    .venv\\Scripts\\python scripts\\bench\\benchmark_large.py run --repeat 3 --window 16 --label window16
    .venv\\Scripts\\python scripts\\bench\\benchmark_large.py run --repeat 1 --label raised-limits \\
        --set DBMAP_MAX_EXPLAIN_COST=10000000 --set DBMAP_MAX_EXPLAIN_ROWS=10000000
    .venv\\Scripts\\python scripts\\bench\\benchmark_large.py conventional --repeat 3
    .venv\\Scripts\\python scripts\\bench\\benchmark_large.py cold --repeat 3
    .venv\\Scripts\\python scripts\\bench\\benchmark_large.py safety
    .venv\\Scripts\\python scripts\\bench\\benchmark_large.py blind

Each subcommand writes docs/benchmarks/large-db/<name>.json. The DBMAP_MAX_EXPLAIN_* overrides
work as process variables only because .env.bench does not set them (the env file wins).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import shutil
import statistics
import sys
from contextlib import redirect_stderr
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVICE_SRC = ROOT / "services" / "dbmap" / "src"
OUT = ROOT / "docs" / "benchmarks" / "large-db"
TASKS = ROOT / "scripts" / "bench" / "large_tasks.json"
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_corpus import (
    CATALOG_SEARCH_SQL,
    compare,
    mcp_payload,
    norm,
    payload_tokens,
)
from dbmap.config import Settings

ROWS_EACH = 200
BATCH_BYTES = 262_144


def require_bench_env() -> None:
    env_file = os.environ.get("DBMAP_ENV_FILE", "")
    if not env_file.endswith(".env.bench"):
        sys.exit("Set DBMAP_ENV_FILE to the .env.bench file first; never benchmark the repo .env.")


def pct(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)], 2)


def stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "p95": pct(values, 0.95),
        "p99": pct(values, 0.99),
        "max": round(max(values), 2),
    }


def nbytes(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":"), default=str))


def direct_rows(sql: str, limit: int = ROWS_EACH) -> tuple[list[str], list[tuple]]:
    """Same wrapping TableFox applies, on a fresh read-only connection as the reader role."""
    import psycopg

    settings = Settings.from_env()
    with psycopg.connect(**settings.connection_kwargs()) as conn:
        conn.execute("set transaction read only")
        conn.execute("set statement_timeout = 60000")
        cursor = conn.execute(f"select * from (\n{sql.rstrip(';')}\n) as q limit {limit}")
        return [c.name for c in cursor.description], cursor.fetchall()


# ---------- MCP session ----------

class Server:
    def __init__(self, extra_env: dict[str, str] | None = None) -> None:
        self.extra_env = extra_env or {}

    async def __aenter__(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = os.environ.copy()
        env["PYTHONPATH"] = str(SERVICE_SRC)
        env.update(self.extra_env)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "dbmap.mcp_server"], cwd=str(ROOT), env=env
        )
        self._stderr = redirect_stderr(io.StringIO())
        self._stderr.__enter__()
        self._client = stdio_client(params)
        read, write = await self._client.__aenter__()

        self._session = ClientSession(read, write)
        self.session = await self._session.__aenter__()
        started = perf_counter()
        await self.session.initialize()
        self.initialize_ms = round((perf_counter() - started) * 1000, 2)
        return self

    async def __aexit__(self, *exc):
        await self._session.__aexit__(*exc)
        await self._client.__aexit__(*exc)
        self._stderr.__exit__(*exc)

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], float, bool]:
        started = perf_counter()
        result = await self.session.call_tool(tool, args)
        elapsed = round((perf_counter() - started) * 1000, 2)
        return mcp_payload(result), elapsed, bool(getattr(result, "is_error", None) or getattr(result, "isError", False))


def load_tasks() -> list[dict[str, Any]]:
    return json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]


def bare(relation_id: str) -> str:
    return relation_id.split(":", 1)[-1]


# ---------- run: retrieval, latency, payload, policy, accuracy ----------

async def run_once(tasks, window: int, extra_env: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    async with Server(extra_env) as server:
        await server.call("database_context_window", {"size": window})
        for index, task in enumerate(tasks):
            ctx, ctx_ms, ctx_err = await server.call(
                "database_task_context",
                {"question": task["question"], "max_relations": 6, "max_bytes": 6144},
            )
            batch, batch_ms, batch_err = await server.call(
                "database_readonly_batch",
                {"queries": task["queries"], "max_rows_each": ROWS_EACH, "max_bytes": BATCH_BYTES},
            )
            structured = ctx["structuredContent"] or {}
            pack = [bare(item["id"]) for item in structured.get("relations", [])]
            known = [bare(item) for item in structured.get("known", [])]
            needed = task["needed_relations"]
            ranks = {name: (pack.index(name) + 1 if name in pack else None) for name in needed}
            results = (batch["structuredContent"] or {}).get("results", [])
            out.append(
                {
                    "task": task["name"],
                    "category": task["category"],
                    "cold": index == 0,
                    "context_ms": ctx_ms,
                    "batch_ms": batch_ms,
                    "total_ms": round(ctx_ms + batch_ms, 2),
                    "context_bytes": nbytes(ctx),
                    "context_tokens": payload_tokens(ctx),
                    "batch_bytes": nbytes(batch),
                    "batch_tokens": payload_tokens(batch),
                    "pack": pack,
                    "known": known,
                    "interpreted": structured.get("interpreted", []),
                    "ranks": ranks,
                    "recall_at_6": sum(1 for n in needed if n in pack[:6]) / len(needed),
                    "recall_pack": sum(1 for n in needed if n in pack or n in known) / len(needed),
                    "all_needed_at_6": all(n in pack[:6] for n in needed),
                    "all_needed_in_pack": all(n in pack or n in known for n in needed),
                    "context_error": ctx["content"] if ctx_err else None,
                    "batch_error": batch["content"] if batch_err else None,
                    "results": [
                        {
                            "name": r["name"],
                            "blocked": r.get("blocked", False),
                            "reason": r.get("reason"),
                            "review": r.get("review", []),
                            "row_count": r.get("row_count"),
                            "truncated": r.get("truncated", False),
                            "rows": r.get("rows", []),
                        }
                        for r in results
                    ],
                }
            )
    return out


def summarise_run(tasks, runs) -> dict[str, Any]:
    flat = [item for run in runs for item in run]
    warm = [item for item in flat if not item["cold"]]
    last = runs[-1]
    by_cat: dict[str, dict[str, Any]] = {}
    for item in last:
        c = by_cat.setdefault(item["category"], {"tasks": 0, "recall_at_6": [], "recall_pack": [],
                                                 "all_at_6": 0, "all_in_pack": 0, "answer_match": 0,
                                                 "blocked": 0, "context_tokens": []})
        c["tasks"] += 1
        c["recall_at_6"].append(item["recall_at_6"])
        c["recall_pack"].append(item["recall_pack"])
        c["all_at_6"] += item["all_needed_at_6"]
        c["all_in_pack"] += item["all_needed_in_pack"]
        c["answer_match"] += item.get("answer_match", False)
        c["blocked"] += any(r["blocked"] for r in item["results"]) or bool(item["batch_error"])
        c["context_tokens"].append(item["context_tokens"])
    categories = {
        name: {
            "tasks": c["tasks"],
            "recall_at_6_mean": round(statistics.mean(c["recall_at_6"]), 3),
            "recall_pack_mean": round(statistics.mean(c["recall_pack"]), 3),
            "tasks_all_needed_at_6": c["all_at_6"],
            "tasks_all_needed_in_pack": c["all_in_pack"],
            "answers_match": c["answer_match"],
            "blocked_or_error": c["blocked"],
            "mean_context_tokens": round(statistics.mean(c["context_tokens"]), 1),
        }
        for name, c in sorted(by_cat.items())
    }
    ranks = [r for item in last for r in item["ranks"].values()]
    return {
        "tasks": len(last),
        "repeats": len(runs),
        "latency_ms_warm": {
            "task_context": stats([i["context_ms"] for i in warm]),
            "readonly_batch": stats([i["batch_ms"] for i in warm]),
            "both_calls": stats([i["total_ms"] for i in warm]),
        },
        "latency_ms_first_task_per_process": stats([i["total_ms"] for i in flat if i["cold"]]),
        "payload": {
            "context_bytes_mean": round(statistics.mean(i["context_bytes"] for i in last), 1),
            "context_tokens_mean": round(statistics.mean(i["context_tokens"] for i in last), 1),
            "context_tokens_median": statistics.median(i["context_tokens"] for i in last),
            "batch_bytes_mean": round(statistics.mean(i["batch_bytes"] for i in last), 1),
            "batch_tokens_mean": round(statistics.mean(i["batch_tokens"] for i in last), 1),
            "both_tokens_mean": round(statistics.mean(i["context_tokens"] + i["batch_tokens"] for i in last), 1),
            "batch_settings": {"max_rows_each": ROWS_EACH, "max_bytes": BATCH_BYTES},
        },
        "retrieval": {
            "needed_relations": len(ranks),
            "recall_at_6_relations": round(sum(1 for r in ranks if r and r <= 6) / len(ranks), 3),
            "recall_pack_relations": round(
                sum(1 for item in last for n in item["ranks"] if n in item["pack"] or n in item["known"]) / len(ranks), 3),
            "tasks_all_needed_at_6": sum(i["all_needed_at_6"] for i in last),
            "tasks_all_needed_in_pack": sum(i["all_needed_in_pack"] for i in last),
            "rank_histogram": {str(k): sum(1 for r in ranks if r == k) for k in range(1, 13)} | {
                "missing": sum(1 for r in ranks if r is None)},
            "misses": {i["task"]: [n for n, r in i["ranks"].items() if r is None and n not in i["known"]]
                       for i in last if not i["all_needed_in_pack"]},
        },
        "policy": {
            "blocked_queries": sum(1 for i in last for r in i["results"] if r["blocked"]),
            "batch_errors": sum(1 for i in last if i["batch_error"]),
            "blocked": {i["task"]: [{"reason": r["reason"], "review": r["review"]} for r in i["results"] if r["blocked"]]
                        for i in last if any(r["blocked"] for r in i["results"])},
            "errors": {i["task"]: i["batch_error"] for i in last if i["batch_error"]},
        },
        "accuracy": {
            "answers_match": sum(1 for i in last if i.get("answer_match")),
            "total": len(last),
            "mismatches": {i["task"]: i.get("compare") for i in last if not i.get("answer_match")},
        },
        "categories": categories,
    }


async def cmd_run(args) -> None:
    tasks = load_tasks()
    extra = dict(item.split("=", 1) for item in args.set)
    runs = [await run_once(tasks, args.window, extra) for _ in range(args.repeat)]
    last = runs[-1]
    by_name = {t["name"]: t for t in tasks}
    for item in last:
        sql = by_name[item["task"]]["queries"][0]["sql"]
        result = item["results"][0] if item["results"] else None
        if not result or result["blocked"]:
            item["answer_match"] = False
            item["compare"] = {"match": False, "why": "blocked" if result else "batch_error"}
            continue
        _, rows = direct_rows(sql)
        item["compare"] = compare(sql, result["rows"], rows)
        item["answer_match"] = item["compare"]["match"]
    summary = summarise_run(tasks, runs)
    summary["config"] = {"label": args.label, "window": args.window, "env_overrides": extra}
    for run in runs:  # keep raw rows only for the last run
        if run is not last:
            for item in run:
                for r in item["results"]:
                    r.pop("rows", None)
    path = OUT / f"run-{args.label}.json"
    path.write_text(json.dumps({"summary": summary, "runs": runs}, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("latency_ms_warm", "payload", "retrieval", "accuracy")}, indent=1, default=str)[:6000])
    print(f"\nwrote {path}")


# ---------- conventional path ----------

def cmd_conventional(args) -> None:
    import psycopg
    from psycopg.rows import dict_row

    tasks = load_tasks()
    settings = Settings.from_env()
    rows = []
    for repeat in range(args.repeat):
        for task in tasks:
            started = perf_counter()
            search_ms = 0.0
            tokens = 0
            with psycopg.connect(**settings.connection_kwargs(), row_factory=dict_row) as conn:
                conn.execute("set transaction read only")
                conn.execute(f"set statement_timeout = {max(1, settings.statement_timeout_ms)}")
                for term in task["catalog_terms"]:
                    s = perf_counter()
                    found = list(conn.execute(CATALOG_SEARCH_SQL, (f"%{term}%",)))
                    search_ms += (perf_counter() - s) * 1000
                    tokens += payload_tokens({"query": term, "results": found})
                for query in task["queries"]:
                    result = list(conn.execute(query["sql"]))
                    tokens += payload_tokens({"query": query["name"], "results": result})
            rows.append({"task": task["name"], "repeat": repeat,
                         "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                         "catalog_search_ms": round(search_ms, 2), "tokens": tokens})
    summary = {
        "tasks": len(tasks), "repeats": args.repeat,
        "latency_ms": stats([r["elapsed_ms"] for r in rows]),
        "catalog_search_ms": stats([r["catalog_search_ms"] for r in rows]),
        "tokens_mean": round(statistics.mean(r["tokens"] for r in rows if r["repeat"] == 0), 1),
        "tokens_median": statistics.median(r["tokens"] for r in rows if r["repeat"] == 0),
        "note": "Fresh connection per task, catalog ILIKE search per catalog term, then the ground-truth SQL unbounded by TableFox policy.",
    }
    (OUT / "conventional.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


# ---------- cold start ----------

async def cmd_cold(args) -> None:
    tasks = load_tasks()
    first = tasks[0]
    cache = ROOT / Settings.from_env().cache_dir
    samples = []
    for repeat in range(args.repeat):
        for with_cache in (False, True):
            if not with_cache:
                shutil.rmtree(cache, ignore_errors=True)
            spawn = perf_counter()
            async with Server() as server:
                _ctx, ctx_ms, _ = await server.call(
                    "database_task_context", {"question": first["question"], "max_relations": 6, "max_bytes": 6144})
                _batch, batch_ms, _ = await server.call(
                    "database_readonly_batch", {"queries": first["queries"], "max_rows_each": 50, "max_bytes": 32768})
                total = round((perf_counter() - spawn) * 1000, 2)
                samples.append({"repeat": repeat, "snapshot_cache": "present" if with_cache else "deleted",
                                "spawn_and_initialize_ms": round(total - ctx_ms - batch_ms, 2),
                                "initialize_ms": server.initialize_ms, "first_task_context_ms": ctx_ms,
                                "first_batch_ms": batch_ms, "spawn_to_first_answer_ms": total})
    summary = {}
    for state in ("deleted", "present"):
        group = [s for s in samples if s["snapshot_cache"] == state]
        summary[state] = {k: stats([s[k] for s in group]) for k in
                          ("spawn_and_initialize_ms", "first_task_context_ms", "first_batch_ms", "spawn_to_first_answer_ms")}
    (OUT / "cold-start.json").write_text(json.dumps({"summary": summary, "samples": samples}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


# ---------- safety ----------

WRITES = {
    "insert": "insert into public.regions (region_id, region_code, region_name) values (99, 'XX', 'x')",
    "update": "update public.regions set region_name = 'x' where region_id = 1",
    "delete": "delete from public.regions where region_id = 1",
    "drop": "drop table public.regions",
    "truncate": "truncate public.regions",
    "create_table": "create table public.tf_probe (id int)",
    "select_into": "select * into public.tf_probe from public.regions",
    "copy": "copy public.regions to stdout",
    "data_modifying_cte": "with gone as (delete from public.regions where region_id = 1 returning *) select * from gone",
    "multi_statement": "select 1; delete from public.regions",
    "set_role": "set role bench_owner",
    "pg_sleep": "select pg_sleep(30)",
    "for_update": "select * from public.regions for update",
    "lo_import": "select lo_import('C:/Windows/win.ini')",
    "set_config": "select set_config('default_transaction_read_only', 'off', false)",
    "pg_read_file": "select pg_read_file('postgresql.conf')",
    "nextval": "select nextval('hr.employees_employee_id_seq')",
}
SENSITIVE = {
    "email": "select email from crm.contacts limit 3",
    "phone": "select phone from crm.contacts limit 3",
    "ssn": "select ssn from hr.employees limit 3",
    "national_id": "select national_id from hr.employees limit 3",
    "iban": "select iban from hr.employees limit 3",
    "bank_iban": "select bank_iban from hr.payroll limit 3",
    "payer_iban": "select payer_iban from finance.payments limit 3",
    "card_last4": "select card_last4 from finance.payments limit 3",
    "tax_id": "select tax_id from crm.accounts limit 3",
    "tax_code": "select tax_code from hr.employees limit 3",
    "salary_amt": "select salary_amt from hr.employees limit 3",
    "payroll_net_amt": "select net_amt from hr.payroll limit 3",
    "password_hash": "select password_hash from ops.app_users limit 3",
    "api_key": "select api_key from ops.api_keys limit 3",
    "key_prefix": "select key_prefix from ops.api_keys limit 3",
    "date_of_birth": "select date_of_birth from hr.employees limit 3",
    "home_address": "select home_address from hr.employees limit 3",
    "ip_address (false positive?)": "select ip_address from ops.login_events limit 3",
    "client_ip": "select client_ip from ops.api_request_log limit 3",
    "select_star_employees": "select * from hr.employees limit 3",
    "alias_bypass_email": "select email as contact from crm.contacts limit 3",
    "expression_bypass_email": "select lower(email) from crm.contacts limit 3",
    "alias_bypass_ssn": "select ssn as x from hr.employees limit 3",
    "view_directory": "select * from hr.v_employee_directory limit 3",
    # Further alias and expression shapes (added with the source-column check).
    "alias_bypass_iban": "select iban as account from hr.employees limit 3",
    "concat_bypass": "select first_name || ' ' || national_id as label from hr.employees limit 3",
    "whole_row_jsonb": "select to_jsonb(e) as doc from hr.employees e limit 3",
    "union_bypass": "select region_name from public.regions union all select email from crm.contacts limit 3",
    "cte_bypass": "with x as materialized (select ssn as s from hr.employees) select s from x limit 3",
    "subquery_bypass": "select s from (select ssn as s from hr.employees limit 5) q",
    "lateral_values_bypass": "select v.x from crm.contacts c, lateral (values (c.email)) v(x) limit 3",
    "scalar_subquery_bypass": "select (select max(bank_iban) from hr.payroll) as v",
}
# Reads that must run: string literals, foreign keys named after secrets, filters and counts.
ALLOWED = {
    "literal_delete": "select count(*) from public.audit_log where action = 'delete'",
    "api_key_id": "select api_key_id, count(*) from ops.api_request_log group by 1 order by 2 desc limit 3",
    "count_employees": "select count(*) from hr.employees",
    "filter_on_email": "select count(*) from crm.contacts where email like '%.com'",
    "directory_view": "select first_name, job_title from hr.v_employee_directory limit 3",
}


async def cmd_safety(args) -> None:
    import psycopg

    def region_fingerprint():
        with psycopg.connect("postgresql://bench_owner@127.0.0.1:55450/bench") as conn:
            return conn.execute("select count(*), md5(string_agg(region_name, ',' order by region_id)), "
                                "to_regclass('public.tf_probe') is null from public.regions").fetchone()

    before = region_fingerprint()
    writes, sensitive = [], []
    async with Server() as server:
        for name, sql in WRITES.items():
            payload, ms, is_error = await server.call(
                "database_readonly_batch", {"queries": [{"name": "probe", "sql": sql}], "max_rows_each": 5})
            result = ((payload["structuredContent"] or {}).get("results") or [{}])[0]
            refused = is_error or result.get("blocked", False)
            writes.append({"case": name, "sql": sql, "refused": refused, "passed": refused,
                           "how": (" ".join(payload["content"])[:300] if is_error else result.get("reason")),
                           "elapsed_ms": ms})
        for name, sql in SENSITIVE.items():
            payload, ms, is_error = await server.call(
                "database_readonly_batch", {"queries": [{"name": "probe", "sql": sql}], "max_rows_each": 3})
            result = ((payload["structuredContent"] or {}).get("results") or [{}])[0]
            sensitive.append({"case": name, "sql": sql, "blocked": bool(is_error or result.get("blocked")),
                              "columns": result.get("columns"), "rows_returned": len(result.get("rows") or []),
                              "reason": " ".join(payload["content"])[:300] if is_error else result.get("reason")})
        allowed = []
        for name, sql in ALLOWED.items():
            payload, ms, is_error = await server.call(
                "database_readonly_batch", {"queries": [{"name": "probe", "sql": sql}], "max_rows_each": 3})
            result = ((payload["structuredContent"] or {}).get("results") or [{}])[0]
            ran = not is_error and not result.get("blocked") and not result.get("error")
            allowed.append({"case": name, "sql": sql, "ran": ran, "rows_returned": len(result.get("rows") or []),
                            "reason": " ".join(payload["content"])[:300] if is_error else (result.get("reason") or result.get("error"))})
    after = region_fingerprint()
    summary = {
        "writes_refused": sum(w["refused"] for w in writes), "writes_total": len(writes),
        "database_unchanged": before == after,
        "sensitive_blocked": [s["case"] for s in sensitive if s["blocked"]],
        "sensitive_returned": [s["case"] for s in sensitive if not s["blocked"]],
        "allowed_ran": [a["case"] for a in allowed if a["ran"]],
        "allowed_refused": [a["case"] for a in allowed if not a["ran"]],
    }
    (OUT / "safety.json").write_text(json.dumps({"summary": summary, "writes": writes, "sensitive": sensitive,
                                                 "allowed": allowed},
                                                indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


# ---------- blind authoring ----------

async def cmd_blind(args) -> None:
    attempts = json.loads((OUT / f"{args.prefix}-attempts.json").read_text(encoding="utf-8"))["attempts"]
    by_name = {t["name"]: t for t in load_tasks()}
    results = {}
    async with Server() as server:
        for name, attempt in attempts.items():
            truth_sql = by_name[name]["queries"][0]["sql"]
            cols, truth = direct_rows(truth_sql)
            entry = {"truth_columns": cols, "truth_rows": [[norm(v) for v in r] for r in truth]}
            if attempt.get("sql"):
                payload, _ms, is_error = await server.call(
                    "database_readonly_batch",
                    {"queries": [{"name": "attempt", "sql": attempt["sql"]}], "max_rows_each": ROWS_EACH,
                     "max_bytes": BATCH_BYTES})
                result = ((payload["structuredContent"] or {}).get("results") or [{}])[0]
                entry.update({
                    "error": " ".join(payload["content"])[:600] if is_error else None,
                    "blocked": result.get("blocked", False), "reason": result.get("reason"),
                    "review": result.get("review"), "columns": result.get("columns"),
                    "rows": [[norm(v) for v in r] for r in (result.get("rows") or [])],
                })
            results[name] = entry
    (OUT / f"{args.prefix}-raw.json").write_text(json.dumps(results, indent=1, default=str) + "\n", encoding="utf-8")
    for name, e in results.items():
        print(name, "ERR" if e.get("error") else ("BLOCKED" if e.get("blocked") else len(e.get("rows", []))),
              (e.get("error") or e.get("reason") or "")[:160])


def main() -> None:
    require_bench_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--repeat", type=int, default=3)
    run.add_argument("--window", type=int, default=0)
    run.add_argument("--label", default="default")
    run.add_argument("--set", action="append", default=[], help="KEY=VALUE env override for the server")
    conv = sub.add_parser("conventional")
    conv.add_argument("--repeat", type=int, default=3)
    cold = sub.add_parser("cold")
    cold.add_argument("--repeat", type=int, default=3)
    sub.add_parser("safety")
    blind = sub.add_parser("blind")
    blind.add_argument("--prefix", default="blind", help="reads <prefix>-attempts.json, writes <prefix>-raw.json")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    handler = {"run": cmd_run, "conventional": cmd_conventional, "cold": cmd_cold,
               "safety": cmd_safety, "blind": cmd_blind}[args.cmd]
    result = handler(args)
    if asyncio.iscoroutine(result):
        asyncio.run(result)


if __name__ == "__main__":
    main()
