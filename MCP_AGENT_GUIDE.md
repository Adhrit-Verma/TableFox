# TableFox agent guide

This guide is for AI agents and LLM clients using TableFox (the Python package is named `dbmap`). It explains how to navigate a PostgreSQL database accurately while keeping context, latency and database load low.

People setting TableFox up should start with the [user guide](docs/USER_GUIDE.md); per-client setup (hosted and self-hosted) is in [docs/AGENTS.md](docs/AGENTS.md).

## Purpose

Use TableFox when you need to understand an unfamiliar PostgreSQL database before writing queries, explaining data, planning migrations, or locating the source of a business concept.

The server exposes stable graph IDs and bounded navigation tools. Prefer incremental discovery over requesting the entire database graph.

## Connect

- **Hosted:** `https://tablefox.onrender.com/mcp` with OAuth sign-in. The user connects databases on TableFox's private page; never ask for credentials in chat.
- **Self-hosted:** run `dbmap-mcp` (stdio) with `DBMAP_ENV_FILE` pointing at a `.env` that holds a read-only `DATABASE_URL`. Example for any `mcpServers`-style client:

```json
{
  "mcpServers": {
    "tablefox": {
      "command": "/path/to/TableFox/.venv/bin/dbmap-mcp",
      "env": { "DBMAP_ENV_FILE": "/path/to/TableFox/.env" }
    }
  }
}
```

Restart the MCP client after changing `.env`, because a running stdio server keeps the configuration loaded by its process.

### Advanced: expose your own single-user server privately

To reach a self-hosted server from a web assistant without the hosted service, run it with `DBMAP_MCP_TRANSPORT=streamable-http` (it binds to `127.0.0.1:8765`) and put an authenticating HTTPS proxy in front of it, for example Cloudflare Tunnel plus Cloudflare Access with Managed OAuth. Set `DBMAP_MCP_PUBLIC_HOST` to the public host name; requests for any other `Host` get `421`. Never expose the port directly. `curl -i https://<host>/mcp` must return `401`, never a tool list.

## Recommended Navigation Workflow

1. Call `database_connectivity_check` once. Confirm the expected database and read-only user before doing other work.
2. Call `database_task_context` with the complete task. It returns ranked relation IDs, matched/key columns, declared joins, interpretations, and a connectivity flag.
3. Treat the ranked relations as candidates. Call `database_source_of_truth` only when the answer requires an authoritative business source; only approved context is verified.
4. Draft at most five named, explicit, bounded `SELECT`/`WITH` statements. Use only declared joins from the context.
5. Call `database_readonly_batch` once. It runs every statement through the same EXPLAIN, schema, join, sensitive-column, timeout, and read-only controls as the single-query tool.
6. Use `database_search`, `database_explain_object`, `database_neighbors`, or `database_find_join_path` only when the compact context is ambiguous or disconnected.
7. Call `database_graph_snapshot` only for broad inventories, not routine data questions.

This sequence is faster and consumes much less model context than loading the full schema first.

## Tool Selection

| Tool | Use it for | Avoid it when |
| --- | --- | --- |
| `database_connectivity_check` | Confirming credentials, server identity, and reachability | Repeating it before every tool call |
| `database_task_context` | Normal task discovery in one compact call | You need a schema-wide inventory |
| `database_readonly_batch` | Executing up to five independent guarded reads in one transaction | Any write, unbounded read, or unresolved join |
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
1. Request task context for "customer lifecycle".
2. Review ranked stable IDs, columns, joins, and connectivity.
3. Check source-of-truth evidence when authority matters.
4. Report important keys and unresolved ambiguity.
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
Use TableFox's two-call path: database_task_context for the complete task, then one database_readonly_batch containing explicit bounded reads. Preserve stable IDs and declared joins. Use database_source_of_truth for authority claims and diagnostic tools only when context is ambiguous or disconnected. Treat the database as production and never request or expose credentials or sensitive row data.
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
