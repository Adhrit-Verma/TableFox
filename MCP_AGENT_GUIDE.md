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

## Connect ChatGPT (personal use, via Cloudflare)

ChatGPT ↔ `https://<host>/mcp` ↔ Cloudflare Access (OAuth) ↔ Cloudflare Tunnel ↔ `http://127.0.0.1:8765/mcp`.

The server only listens on loopback and has no login of its own; Cloudflare Access is the only gate. Never expose port 8765 any other way, and never use a quick tunnel (`cloudflared tunnel --url`).

Each person runs their own TableFox with their own database, tunnel, and ChatGPT account. Credentials stay in a local file on that machine and are never typed into ChatGPT.

1. **Run the server over HTTP with its own credentials.** Create a read-only role for your database (README "Safety model"); with ChatGPT in the loop it is the second line of defense if anything above it fails. Then create `.env.chatgpt` in the repo root (git-ignored) holding only what this server needs, so your everyday `.env` is never exposed:

   ```
   DATABASE_URL=postgresql://dbmap_reader:<password>@<db-host>:5432/<database>
   DBMAP_MCP_TRANSPORT=streamable-http
   DBMAP_MCP_PORT=8765
   DBMAP_MCP_PUBLIC_HOST=<host>
   DBMAP_MCP_ACTOR=chatgpt
   DBMAP_AUDIT_DIR=.logs/audit-chatgpt
   DBMAP_RUNTIME_FILE=.tablefox-runtime-chatgpt.json
   ```

   Then `$env:DBMAP_ENV_FILE = "$PWD\.env.chatgpt"; .\scripts\run_mcp.ps1`. Without `DBMAP_ENV_FILE` the server falls back to `.env`. Requests whose `Host` is not loopback or `<host>` get `421`.
2. **Tunnel.** Cloudflare dashboard → Networking → Tunnels → Create a tunnel → Windows → run the generated install command in an admin terminal. Routes → Add route → Published application: your subdomain/domain, no path, Service URL `http://127.0.0.1:8765`.
3. **Access app.** Zero Trust → Access controls → AI controls → MCP servers → Add an MCP server. HTTP URL `https://<host>/mcp`, policy Allow → Emails → your email only.
4. **Managed OAuth.** Applications → that app → Edit → Advanced settings → turn on Managed OAuth. Allowed redirect URIs:
   - `https://chatgpt.com/connector_platform_oauth_redirect`
   - `https://chatgpt.com/connector/oauth/*`

   Copy the app's AUD tag (Configure → Additional settings).
5. **Verify the Access token at the tunnel.** Tunnels → your tunnel → Routes → Edit route → Additional application settings → Access → Protect with Access: on, team name, AUD tag. `cloudflared` then rejects any request without a valid `Cf-Access-Jwt-Assertion`.
6. **Check from outside.** `curl -i https://<host>/mcp` must return `401` with a `WWW-Authenticate` header, never a 200 or a tool list.
7. **ChatGPT.** Settings → Security and login → Developer mode on. chatgpt.com/plugins → + → Add custom MCP server → URL `https://<host>/mcp`, auth OAuth → Create as a plugin. Sign in through Cloudflare when prompted, then `@` it in a new chat.

Every ChatGPT call is audited with actor `chatgpt`. Revoke access by disabling the Access policy or stopping the tunnel (`sc stop cloudflared`).

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
Use Database Agent's two-call path: database_task_context for the complete task, then one database_readonly_batch containing explicit bounded reads. Preserve stable IDs and declared joins. Use database_source_of_truth for authority claims and diagnostic tools only when context is ambiguous or disconnected. Treat the database as production and never request or expose credentials or sensitive row data.
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
