from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr
import io
import json
import math
import os
from pathlib import Path
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
    """Comparable estimate when a provider tokenizer is not available."""
    rendered = json.dumps(payload, separators=(",", ":"), default=str)
    return math.ceil(len(rendered) / 4)


def mcp_payload(result: Any) -> dict[str, Any]:
    texts = [item.text for item in result.content if hasattr(item, "text")]
    return {"structuredContent": result.structuredContent, "content": texts}


async def tablefox_context(question: str) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVICE_SRC)
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dbmap.mcp_server"],
        cwd=ROOT,
        env=env,
    )
    calls: list[dict[str, Any]] = []
    total_start = perf_counter()
    with redirect_stderr(io.StringIO()):
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                started = perf_counter()
                result = await session.call_tool(
                    "database_task_context",
                    {"question": question, "max_relations": 6, "max_bytes": 6144},
                )
                payload = mcp_payload(result)
                calls.append(
                    {
                        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                        "estimated_payload_tokens": payload_tokens(payload),
                        "payload": payload,
                    }
                )
    return {
        "method": "Tablefox MCP database_task_context",
        "elapsed_ms": round((perf_counter() - total_start) * 1000, 2),
        "estimated_payload_tokens": sum(
            call["estimated_payload_tokens"] for call in calls
        ),
        "calls": calls,
    }


def conventional_search(terms: list[str]) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    settings = Settings.from_env()
    calls: list[dict[str, Any]] = []
    total_start = perf_counter()
    with psycopg.connect(**settings.connection_kwargs(), row_factory=dict_row) as conn:
        conn.execute("set transaction read only")
        conn.execute(f"set statement_timeout = {max(1, settings.statement_timeout_ms)}")
        for term in terms:
            started = perf_counter()
            rows = list(conn.execute(CATALOG_SEARCH_SQL, (f"%{term}%",)))
            payload = {"query": term, "results": rows}
            calls.append(
                {
                    "term": term,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                    "estimated_payload_tokens": payload_tokens(payload),
                    "payload": payload,
                }
            )
    return {
        "method": "Conventional information_schema search",
        "elapsed_ms": round((perf_counter() - total_start) * 1000, 2),
        "estimated_payload_tokens": sum(
            call["estimated_payload_tokens"] for call in calls
        ),
        "calls": calls,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "terms",
        nargs="*",
        default=["crew", "document", "rest", "flight time", "duty period"],
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".dbmap-cache" / "benchmark.json"
    )
    parser.add_argument(
        "--question",
        default=(
            "Capt. David Federigo: documents, rest periods, flight time, and duty "
            "periods for the last year"
        ),
    )
    args = parser.parse_args()
    report = {
        "question": args.question,
        "measurement": {
            "time": "client-observed wall time",
            "tokens": "ceil(compact MCP content plus structuredContent characters / 4); estimate, not model billing",
            "scope": "schema discovery only; data-query correctness must be benchmarked separately",
        },
        "tablefox": await tablefox_context(args.question),
        "conventional": conventional_search(args.terms),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "tablefox": {
            "elapsed_ms": report["tablefox"]["elapsed_ms"],
            "estimated_payload_tokens": report["tablefox"]["estimated_payload_tokens"],
        },
        "conventional": {
            "elapsed_ms": report["conventional"]["elapsed_ms"],
            "estimated_payload_tokens": report["conventional"]["estimated_payload_tokens"],
        },
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
