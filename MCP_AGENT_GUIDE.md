# Database Agent MCP Guide

This document is written for AI agents and LLM clients using the Database Agent MCP server. It describes how to navigate a PostgreSQL database accurately while keeping context, latency, and database load low.

## Purpose

Use Database Agent when you need to understand an unfamiliar PostgreSQL database before writing queries, explaining data, planning migrations, or locating the source of a business concept.

The server exposes stable graph IDs and bounded navigation tools. Prefer incremental discovery over requesting the entire database graph.

## Connect the MCP Server

The server reads database credentials from the repository's ignored `.env` file. Do not place passwords in prompts, committed MCP configuration, or chat messages.

```json
{
  "mcpServers": {
    "dbmap-postgres": {
      "command": "powershell",
      "args": [
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:\\Code\\AI Agents\\Database Agent\\scripts\\run_mcp.ps1"
      ]
    }
  }
}
```

Restart the MCP client after changing `.env`, because a running stdio server keeps the configuration loaded by its process.

## Recommended Navigation Workflow

1. Call `database_connectivity_check` once. Confirm the expected database and read-only user before doing other work.
2. Call `database_search` with a business term, table fragment, column name, constraint, or comment. Start with a small result limit.
3. Call `database_explain_object` on the best stable ID. This returns important columns and relationship direction in a compact response.
4. Call `database_source_of_truth` when the task needs an authoritative object. Treat only `verified` context as authoritative.
5. Call `database_find_join_path` before drafting a multi-table query.
6. Call `database_neighbors` when you need broader local context. Start at depth 1 and increase only when necessary.
7. Call `database_graph_snapshot` only when the task needs a broad schema inventory. Use schema filters and a bounded maximum node count.
8. Call `database_explain_query` on a proposed query. Review estimated rows, cost, sequential scans, and whether approval is required. This does not execute the query.
9. Call `database_readonly_query` only after the plan is within policy. Select explicit columns and use narrow predicates.

This sequence is faster and consumes much less model context than loading the full schema first.

## Tool Selection

| Tool | Use it for | Avoid it when |
| --- | --- | --- |
| `database_connectivity_check` | Confirming credentials, server identity, and reachability | Repeating it before every tool call |
| `database_search` | Finding objects from names, comments, data types, or business language | You already have the exact stable ID |
| `database_explain_object` | Understanding one table/view, its columns, and relationships | You need several hops of graph context |
| `database_neighbors` | Discovering nearby tables and join paths | You need a database-wide inventory |
| `database_graph_snapshot` | Audits, documentation, architecture summaries, and schema-wide analysis | A focused search can answer the question |
| `database_explain_query` | Checking a proposed query plan without executing it | You need actual result rows |
| `database_readonly_query` | Validating assumptions or retrieving a small result set | Schema discovery or any write operation |
| `database_find_join_path` | Proving a join route from catalog relationships | You only need nearby general context |
| `database_source_of_truth` | Finding approved authoritative objects and uncertainty | You only need a name match |
| `database_schema_changes` | Reviewing migration impact against a configured baseline | No baseline is configured |
| `database_context_identity` | Preparing a context manifest for this exact schema | Routine navigation |

## Stable IDs

Tool responses return IDs that should be passed directly into later calls. Do not reconstruct IDs if the server already returned one.

Common forms include:

```text
schema:crm
table:crm.customers
view:billing.open_invoices
column:crm.customers.email
constraint:crm.accounts.accounts_customer_id_fkey
index:crm.customers.customers_email_key
```

IDs are the navigation contract. Human-readable labels may be duplicated across schemas, while IDs remain unambiguous.

## Query Safety

- Treat the connected database as production unless the user explicitly says otherwise.
- Use a dedicated read-only PostgreSQL role.
- Never attempt `INSERT`, `UPDATE`, `DELETE`, `MERGE`, DDL, transaction-control statements, or multi-statement SQL.
- Prefer metadata tools over querying PostgreSQL catalogs manually.
- For data queries, select named columns instead of `SELECT *`.
- Add restrictive predicates and request the smallest useful row limit.
- Do not retrieve secrets, password hashes, tokens, personal data, or large text/blob columns unless the user explicitly needs them and is authorized.
- Treat sensitive-column detection and approved context classification as defense in depth, not a substitute for column-level privileges.
- Summarize query results; do not flood the model context with raw rows.

The query tools enforce SELECT/CTE-only SQL, block known state-changing functions and row locks, use read-only transactions, apply statement and lock timeouts, cap returned rows, enforce schema policy, and verify multi-relation paths. Sensitive or policy-classified result columns are blocked by default. MCP cannot override blocked plans; an authenticated API administrator must approve non-schema policy exceptions.

## Efficient Agent Patterns

### Understand a business concept

```text
1. Search for "customer lifecycle".
2. Explain the most relevant table and view IDs.
3. Inspect depth-1 neighbors for each candidate.
4. Report the likely source of truth, important keys, and unresolved ambiguity.
```

### Build a join safely

```text
1. Search for both business entities.
2. Explain each table.
3. Use `database_find_join_path` to confirm the foreign-key path and direction.
4. Draft SQL with explicit columns and aliases.
5. Check the draft with `database_explain_query`.
6. Validate with a small read-only query limit only when the plan is acceptable.
```

### Document a schema

```text
1. Request a graph snapshot filtered to one schema.
2. Group tables by relationship clusters.
3. Explain central tables and views individually.
4. Describe keys, inbound/outbound dependencies, and isolated objects.
```

### Investigate an unfamiliar column

```text
1. Search the exact column name and close synonyms.
2. Explain each owning table.
3. Compare data type, nullability, comments, indexes, and relationships.
4. Query only a small aggregate or sample if metadata is insufficient.
```

## Suggested System Instruction

An MCP client can include this compact instruction:

```text
Use Database Agent incrementally. Check connectivity once, search before taking a full snapshot, preserve stable IDs, use database_source_of_truth for authority claims, and call database_find_join_path before writing joins. Run database_explain_query before database_readonly_query. Use explicit columns and bounded results. Treat the database as production and never request or expose credentials or sensitive row data.
```

## Troubleshooting

- Wrong database: `DATABASE_URL` overrides all individual `PG*` variables. Update or remove it, then restart the MCP client.
- Authentication failure: verify the username/password and that the role has `CONNECT` on the database.
- Empty schemas or tables: grant `USAGE` on the schemas and `SELECT` on their tables to the read-only role.
- TLS failure: hosted PostgreSQL commonly requires `PGSSLMODE=require` or an SSL option in `DATABASE_URL`.
- Timeout: narrow schema filters, reduce graph depth/node limits, or simplify the data query.
- Stale graph: request a refreshed snapshot or restart the server after schema changes.
- Context mismatch: run `dbmap-identity` and update both identity fields before trusting code links.
- Schema review unavailable: set `DBMAP_BASELINE_FILE` or pass a baseline to `dbmap-review`.

## Human Visual Companion

Run `.\run.cmd` from the repository root and open `http://localhost:3000`. The visual map uses the same graph engine as the MCP server, so humans and agents can discuss the same stable object IDs and relationships.
