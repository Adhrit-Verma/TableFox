<p align="center">
  <img src="./assets/tablefox-logo.png" alt="TableFox logo" width="150" />
</p>

<h1 align="center">TableFox</h1>

<p align="center">
  <strong>Local PostgreSQL schema intelligence for AI agents.</strong>
</p>

<p align="center">
  TableFox maps your database into a searchable graph so humans and AI agents can understand tables, columns, keys, relationships, and safe query paths without dumping the whole schema into every prompt.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/FastAPI-Backend-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/PostgreSQL-Read--Only-4169E1?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL" />
  <img src="https://img.shields.io/badge/Next.js-UI-000000?style=flat-square&logo=nextdotjs&logoColor=white" alt="Next.js" />
  <img src="https://img.shields.io/badge/MCP-Agent%20Tools-7C3AED?style=flat-square" alt="MCP tools" />
  <img src="https://img.shields.io/badge/Local--First-Safe%20by%20Default-16A34A?style=flat-square" alt="Local first" />
</p>

---

## Why TableFox exists

AI agents are useful with databases, but they usually struggle with one simple problem: **they do not know the schema unless you give it to them**.

The common approach is to paste a giant schema dump into the prompt. That works for small demos, but it becomes expensive, slow, and noisy as the database grows.

TableFox gives agents a better path:

```text
Search only what is needed
→ inspect related tables
→ understand joins and constraints
→ run guarded read-only queries
→ return a focused answer
```

That means agents can work with **smaller context**, **fewer retries**, and **less prompt bloat**.

> TableFox is designed to reduce token usage for AI agents by replacing repeated full-schema prompts with targeted MCP tool calls such as search, neighbors, object explanation, and guarded read-only queries.

---

## What it does

TableFox turns a PostgreSQL database into an agent-friendly graph.

It maps:

| Database object | Examples |
|---|---|
| Schemas | `public`, `auth`, `billing` |
| Tables and views | `employees`, `attendance`, `orders_view` |
| Columns | names, types, nullability, defaults |
| Keys and constraints | primary keys, foreign keys, unique constraints |
| Indexes | indexed columns and index metadata |
| Relationships | table-to-table links through foreign keys |

Then it exposes that map through:

| Interface | Purpose |
|---|---|
| **Next.js UI** | Browse the schema visually in a local 2D graph |
| **FastAPI API** | Serve graph, search, node details, and live updates |
| **MCP server** | Let AI agents search, traverse, explain, and query safely |
| **Read-only query tool** | Allow guarded `SELECT` / `WITH` queries only |

---

## Architecture

```mermaid
flowchart LR
    A[(PostgreSQL)] --> B[TableFox Graph Engine]
    B --> C[FastAPI API]
    B --> D[MCP Stdio Server]
    C --> E[Next.js Graph UI]
    D --> F[AI Agent / MCP Client]
    F --> D
    E --> C
```

The agent does not need to guess the schema. It can ask TableFox first.

```text
User: Which tables are needed for an attendance report?
Agent: database_search("attendance employee department")
Agent: database_neighbors("table:attendance")
Agent: database_explain_object("public.attendance")
Agent: database_readonly_query("SELECT ... LIMIT 50")
```

---

## Token-saving workflow for AI agents

Without a schema map, agents often need this:

```text
Paste entire database schema
Paste table definitions again
Ask model to infer relationships
Fix hallucinated table names
Retry broken SQL
```

With TableFox, the agent can request only the relevant slice:

| Agent need | TableFox tool |
|---|---|
| Find matching tables or columns | `database_search` |
| Inspect relationships | `database_neighbors` |
| Understand one object | `database_explain_object` |
| Get graph summary | `database_graph_snapshot` |
| Validate data through SQL | `database_readonly_query` |

This is useful for:

- smaller prompts
- lower token usage
- fewer hallucinated table names
- safer SQL generation
- faster debugging
- better agent reliability on large databases

---

## Core features

| Feature | Description |
|---|---|
| **Local-first** | Runs against your local or private PostgreSQL database |
| **Read-only by default** | Works best with a dedicated PostgreSQL reader role |
| **Schema graph builder** | Builds a stable graph of tables, views, columns, constraints, indexes, and foreign keys |
| **MCP-ready** | Exposes database understanding tools for AI agents |
| **Interactive graph UI** | Cytoscape-powered 2D schema map with search, filters, refresh status, and inspector panel |
| **Guarded SQL execution** | Allows only protected `SELECT` / `WITH` statements with row limits and timeout controls |
| **Non-executing query plans** | Uses `EXPLAIN` without `ANALYZE` and reports cost, row, and scan policy signals |
| **Sensitive-column guard** | Blocks common credential and PII-like result column names unless an operator opts in |
| **Verified workflows** | Finds declared join paths and distinguishes approved source-of-truth evidence from heuristics |
| **Approved context** | Links business documents, owners, classifications, ORM models, migrations, consumers, and saved queries |
| **Schema impact** | Compares a baseline with PostgreSQL and reports structural changes and confirmed dependencies |
| **Governance** | Supports schema allow/deny rules, aggregate-only usage telemetry, per-user API roles, and JSONL audit logs |
| **One-command launcher** | Starts API and web UI together on Windows |
| **WebSocket updates** | Streams local graph refresh/status updates to the UI |

---

## Project structure

```text
apps/web/             Next.js local browser UI
services/dbmap/       Python graph engine, FastAPI API, MCP server
scripts/              Local PowerShell helper scripts
run.cmd               One-command launcher
.env.example          Environment template
MCP_AGENT_GUIDE.md    Agent workflow and safety guide
```

---

## Quick start

For a database already configured in `.env`:

```powershell
.\run.cmd
```

This starts the local API and web UI, then opens:

```text
http://localhost:3000
```

For first-time setup:

```powershell
.\run.cmd -Install
```

Useful launcher modes:

```powershell
.\run.cmd -Check       # Validate credentials and build a graph, then exit
.\run.cmd -NoBrowser   # Start services without opening the browser
```

---

## Manual setup

Create and activate a Python environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the backend:

```powershell
pip install -e "services/dbmap[dev]"
```

Install the web app dependencies:

```powershell
npm install
```

Copy the environment template:

```powershell
Copy-Item .env.example .env
```

Keep real credentials in `.env`. Do not commit `.env`.

---

## Environment variables

Use either `DATABASE_URL` or individual PostgreSQL variables.

### Option 1: `DATABASE_URL`

```env
DATABASE_URL=postgresql://dbmap_reader:password@localhost:5432/your_database
```

### Option 2: individual PostgreSQL variables

```env
PGHOST=localhost
PGPORT=5432
PGDATABASE=your_database
PGUSER=dbmap_reader
PGPASSWORD=replace-with-a-strong-password
```

If `DATABASE_URL` is present, it takes priority over the individual `PG*` variables.

Query safety policy can be tuned without changing code:

```env
DBMAP_STATEMENT_TIMEOUT_MS=5000
DBMAP_MAX_QUERY_ROWS=200
DBMAP_MAX_EXPLAIN_COST=100000
DBMAP_MAX_EXPLAIN_ROWS=100000
DBMAP_ALLOW_SENSITIVE_DATA=false
```

Additional production controls are shown in `.env.example`. Keep `DBMAP_API_HOST` on `127.0.0.1`, `localhost`, or `::1`; TableFox remains local-only even when API authentication is enabled.

### Approved context

Run `dbmap-identity`, copy `tablefox-context.example.json` to `.tablefox-context.json`, and replace its database identity and schema fingerprint. Business links require a source and update date. ORM and migration links are omitted automatically unless both the database identity and schema fingerprint match.

### API users

Run `dbmap-create-key` once per user. Store only the printed SHA-256 value in a local `tablefox-auth.json` based on `tablefox-auth.example.json`, then set:

```env
DBMAP_AUTH_REQUIRED=true
DBMAP_AUTH_FILE=tablefox-auth.json
```

Roles are `viewer`, `analyst`, `data_reader`, and `admin`. Only administrators can approve a query outside the low-risk cost or relationship policy. Restricted schemas cannot be overridden.

### Schema baselines

```powershell
dbmap-save-baseline
dbmap-review --baseline .dbmap-cache/baseline.snapshot.json --output schema-impact.json
```

The repository includes quality and manually triggered schema-impact GitHub Actions workflows. The impact workflow requires the `TABLEFOX_DATABASE_URL` repository secret.

---

## Running services manually

Start the API:

```powershell
.\scripts\run_api.ps1
```

Start the browser UI:

```powershell
.\scripts\run_web.ps1
```

Start the MCP stdio server:

```powershell
.\scripts\run_mcp.ps1
```

---

## MCP client configuration

Example local MCP configuration:

```json
{
  "mcpServers": {
    "tablefox-postgres": {
      "command": "powershell",
      "args": [
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:\\Code\\AI Agents\\TableFox\\scripts\\run_mcp.ps1"
      ]
    }
  }
}
```

The MCP process loads the repository-root `.env`, so database credentials do not need to be duplicated in the client configuration.

---

## MCP tools

| Tool | Purpose |
|---|---|
| `database_connectivity_check` | Tests database access and validates configuration |
| `database_graph_snapshot` | Returns the current schema graph summary |
| `database_search` | Searches schemas, tables, columns, views, and related objects |
| `database_neighbors` | Finds nearby connected graph nodes |
| `database_explain_object` | Explains a selected table, column, view, or constraint |
| `database_explain_query` | Plans read-only SQL without executing it and applies safety thresholds |
| `database_readonly_query` | Runs guarded read-only SQL queries |
| `database_find_join_path` | Finds a bounded path backed by foreign keys or catalog dependencies |
| `database_source_of_truth` | Returns verified or unresolved authoritative-table candidates with evidence |
| `database_schema_changes` | Compares the configured baseline with the current database |
| `database_context_identity` | Returns the safe database identity and schema fingerprint for context files |

`database_explain_query` uses `EXPLAIN` without `ANALYZE`, so it does not fetch result rows. `database_readonly_query` accepts only guarded `SELECT` or `WITH` statements, blocks known state-changing functions and row locks, applies a row limit, and runs inside a read-only transaction with statement and lock timeouts.

Sensitive-column detection uses both conservative name checks and approved context classifications. It is defense in depth, not a replacement for a least-privilege PostgreSQL role or column-level grants. Multi-relation data queries must also match a declared relationship path.

---

## Safe PostgreSQL reader role

For an existing database, create a dedicated read-only role:

```sql
create role dbmap_reader login password 'replace-with-a-strong-password';
grant connect on database your_database to dbmap_reader;
grant usage on schema public to dbmap_reader;
grant select on all tables in schema public to dbmap_reader;
alter default privileges in schema public grant select on tables to dbmap_reader;
```

Repeat the schema grants for every schema you want TableFox to map.

---

## API endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | API health check |
| `GET` | `/graph` | Full graph response |
| `GET` | `/graph/search?q=customers` | Search graph objects |
| `GET` | `/graph/node/{node_id}` | Inspect one graph node |
| `GET` | `/graph/identity` | Get context identity and schema fingerprint |
| `GET` | `/graph/changes` | Compare the configured baseline |
| `POST` | `/query/explain` | Assess a read-only query plan without executing it |
| `POST` | `/query/readonly` | Run a role-authorized guarded query |
| `POST` | `/workflow/join-path` | Find a verified relationship path |
| `GET` | `/workflow/source-of-truth?q=customers` | Rank authoritative candidates with evidence |
| `WS` | `/graph/live` | Live graph/status stream |

---

## Example agent prompts

```text
Find all tables related to employees and explain their relationships.
```

```text
Search the schema for document expiry fields and show the connected tables.
```

```text
Which tables should I query to build an attendance report?
```

```text
Run a safe read-only query to count employees department-wise.
```

---

## Tests

```powershell
python -m pytest services/dbmap/tests
```

Use [FUNCTIONAL_CHECKLIST.md](./FUNCTIONAL_CHECKLIST.md) for the complete purpose-driven release and acceptance check.

---

## Roadmap

- [ ] Graph export as JSON
- [ ] Mermaid ER diagram export
- [ ] AI-generated table documentation
- [ ] PostgreSQL performance hints for indexes and constraints
- [ ] External identity-provider integration for hosted deployments

---

## Resume line

> Built **TableFox**, a local-first PostgreSQL schema intelligence tool using Python, FastAPI, Next.js, Cytoscape, and MCP to help AI agents search, traverse, explain, and safely query relational database structures while reducing full-schema prompt overhead.

---

## Small visual touch

<p align="center">
  <img src="https://media.giphy.com/media/jLrRvwKSPsf7urOUDq/giphy.gif" width="300" alt="AI workflow animation from GIPHY" />
</p>

<p align="center">
  <strong>Give your AI agent a map before it queries the database.</strong>
</p>
