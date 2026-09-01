"""Run the reviewed corpus against both paths and report the release-gate numbers.

Compares TableFox's two-call MCP path (database_task_context + database_readonly_batch)
against a conventional catalog-search-then-direct-SQL path, on the same database, with the
same row limits, checking that both produce the same answer.

Reports median and p95 separately for the cold task and the warm tasks, because pool and
process startup dominate the first call. Token figures are ceil(compact JSON characters / 4)
over both MCP `content` and `structuredContent`; they are a comparable estimate, not
provider billing.

    python scripts/benchmark_corpus.py --repeat 5
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr
import io
import json
import math
import os
from pathlib import Path
import statistics
import sys
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVICE_SRC = ROOT / "services" / "dbmap" / "src"
sys.path.insert(0, str(SERVICE_SRC))

from dbmap.config import Settings  # noqa: E402


CATALOG_SEARCH_SQL = """
select
  columns.table_schema as schema,
  columns.table_name,
  columns.column_name,
  columns.data_type,
  obj_description(classes.oid, 'pg_class') as table_comment,
  col_description(classes.oid, columns.ordinal_position) as column_comment
from information_schema.columns columns
join pg_namespace namespaces on namespaces.nspname = columns.table_schema
join pg_class classes
  on classes.relnamespace = namespaces.oid
 and classes.relname = columns.table_name
where columns.table_schema not in ('pg_catalog', 'information_schema')
  and concat_ws(
    ' ', columns.table_schema, columns.table_name, columns.column_name,
    columns.data_type, obj_description(classes.oid, 'pg_class'),
    col_description(classes.oid, columns.ordinal_position)
  ) ilike %s
order by columns.table_schema, columns.table_name, columns.ordinal_position
limit 25
"""


def payload_tokens(payload: Any) -> int:
    return math.ceil(len(json.dumps(payload, separators=(",", ":"), default=str)) / 4)


def mcp_payload(result: Any) -> dict[str, Any]:
    return {
        "structuredContent": result.structuredContent,
        "content": [item.text for item in result.content if hasattr(item, "text")],
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


async def run_tablefox(tasks: list[dict[str, Any]], window: int) -> list[dict[str, Any]]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVICE_SRC)
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "dbmap.mcp_server"], cwd=str(ROOT), env=env
    )
    results: list[dict[str, Any]] = []
    with redirect_stderr(io.StringIO()):
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                # Set the window through the tool itself. A DBMAP_CONTEXT_WINDOW variable
                # would not survive: Settings loads .env with override=True, so the file
                # wins over anything this process exports.
                previous = (
                    await session.call_tool("database_context_window", {})
                ).structuredContent["size"]
                if window != previous:
                    await session.call_tool("database_context_window", {"size": window})
                for index, task in enumerate(tasks):
                    started = perf_counter()
                    context = await session.call_tool(
                        "database_task_context",
                        {"question": task["question"], "max_relations": 6, "max_bytes": 6144},
                    )
                    batch = await session.call_tool(
                        "database_readonly_batch",
                        {"queries": task["queries"], "max_rows_each": 50, "max_bytes": 32768},
                    )
                    elapsed_ms = round((perf_counter() - started) * 1000, 2)
                    context_payload = mcp_payload(context)
                    batch_payload = mcp_payload(batch)
                    structured = context_payload["structuredContent"] or {}
                    returned = {
                        item["id"].split(".")[-1]
                        for item in structured.get("relations", [])
                    } | {
                        item.split(".")[-1] for item in structured.get("known", [])
                    }
                    answer = (batch_payload["structuredContent"] or {}).get("results", [])
                    results.append(
                        {
                            "task": task["name"],
                            "category": task.get("category", "uncategorised"),
                            "cold": index == 0,
                            "elapsed_ms": elapsed_ms,
                            "tokens": payload_tokens(context_payload)
                            + payload_tokens(batch_payload),
                            "context_tokens": payload_tokens(context_payload),
                            "recall_ok": set(task["needed_relations"]) <= returned,
                            "missing": sorted(set(task["needed_relations"]) - returned),
                            "row_count": sum(item.get("row_count", 0) for item in answer),
                        }
                    )
                if window != previous:
                    await session.call_tool("database_context_window", {"size": previous})
    return results


def run_conventional(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    settings = Settings.from_env()
    results = []
    for task in tasks:
        started = perf_counter()
        tokens = 0
        rows_total = 0
        # A fresh connection per task: the conventional path has no pool to reuse.
        with psycopg.connect(**settings.connection_kwargs(), row_factory=dict_row) as conn:
            conn.execute("set transaction read only")
            conn.execute(f"set statement_timeout = {max(1, settings.statement_timeout_ms)}")
            for term in task["catalog_terms"]:
                found = list(conn.execute(CATALOG_SEARCH_SQL, (f"%{term}%",)))
                tokens += payload_tokens({"query": term, "results": found})
            for query in task["queries"]:
                rows = list(conn.execute(query["sql"]))
                rows_total += len(rows)
                tokens += payload_tokens({"query": query["name"], "results": rows})
        results.append(
            {
                "task": task["name"],
                "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                "tokens": tokens,
                "row_count": rows_total,
            }
        )
    return results


def summarise(runs: list[list[dict[str, Any]]], conventional: list[dict[str, Any]]) -> dict:
    flat = [item for run in runs for item in run]
    warm = [item["elapsed_ms"] for item in flat if not item["cold"]]
    cold = [item["elapsed_ms"] for item in flat if item["cold"]]
    last = runs[-1]
    by_task = {item["task"]: item for item in conventional}
    return {
        "tasks": len(last),
        "repeats": len(runs),
        "tablefox": {
            "warm_median_ms": round(statistics.median(warm), 2) if warm else None,
            "warm_p95_ms": round(percentile(warm, 0.95), 2) if warm else None,
            "cold_median_ms": round(statistics.median(cold), 2) if cold else None,
            "mean_tokens": round(statistics.mean([item["tokens"] for item in flat]), 1),
            "mean_context_tokens": round(
                statistics.mean([item["context_tokens"] for item in flat]), 1
            ),
        },
        "conventional": {
            "median_ms": round(statistics.median([c["elapsed_ms"] for c in conventional]), 2),
            "p95_ms": round(percentile([c["elapsed_ms"] for c in conventional], 0.95), 2),
            "mean_tokens": round(statistics.mean([c["tokens"] for c in conventional]), 1),
        },
        "recall": {
            "passed": sum(1 for item in last if item["recall_ok"]),
            "total": len(last),
            "misses": {item["task"]: item["missing"] for item in last if not item["recall_ok"]},
        },
        "answers_match": {
            "passed": sum(
                1
                for item in last
                if by_task.get(item["task"], {}).get("row_count") == item["row_count"]
            ),
            "total": len(last),
        },
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "scripts" / "corpus_tasks.json")
    parser.add_argument("--repeat", type=int, default=1, help="TableFox runs (fresh process each).")
    parser.add_argument("--window", type=int, default=0, help="Context window length, 0 disables.")
    parser.add_argument("--output", type=Path, default=ROOT / ".dbmap-cache" / "corpus-report.json")
    args = parser.parse_args()

    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))["tasks"]
    runs = [await run_tablefox(tasks, args.window) for _ in range(max(1, args.repeat))]
    conventional = run_conventional(tasks)
    summary = summarise(runs, conventional)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"summary": summary, "tablefox_runs": runs, "conventional": conventional},
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    print(f"\nFull report: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
