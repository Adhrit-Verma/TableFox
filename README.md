# Database Agent

Local PostgreSQL graph mapping for AI agents and humans.

The project contains a Python graph engine with FastAPI and MCP stdio entry points, plus a Next.js browser UI for an interactive 2D schema map.

## What It Does

- Connects to PostgreSQL with read-only credentials by default.
- Builds a stable graph of schemas, tables, views, columns, constraints, indexes, and foreign keys.
- Exposes MCP tools so AI agents can search and traverse the database quickly.
- Serves a local API and WebSocket stream for the browser map.
- Shows a Cytoscape-powered 2D graph with search, schema filters, live refresh status, and an inspector panel.

## Project Layout

```text
apps/web              Next.js local browser UI
services/dbmap        Python graph engine, FastAPI API, MCP server
scripts               Local PowerShell helpers
```

## One-command Start

For a database already configured in `.env`:

```powershell
.\run.cmd
```

The launcher validates the database, starts the API and web UI, opens `http://localhost:3000`, and keeps both services attached to the terminal. Press Ctrl+C to stop the services it started.

For a first installation, configure `.env`, then install dependencies and start everything:

```powershell
.\run.cmd -Install
```

Useful launcher modes:

```powershell
.\run.cmd -Check       # Validate credentials and build a graph, then exit
.\run.cmd -NoBrowser   # Start without opening a browser
```

The first install can take a few minutes. Later runs only need `.\run.cmd`.

## Manual Setup

Create a Python environment and install the backend:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e "services/dbmap[dev]"
```

Install the web app dependencies:

```powershell
npm install
```

Copy the example environment file and adjust credentials:

```powershell
Copy-Item .env.example .env
```

Real credentials should stay in `.env`, which is ignored by Git.

Configure either `DATABASE_URL` or the individual `PG*` variables, not both. If `DATABASE_URL` is present, PostgreSQL uses it instead of `PGHOST`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`. The launcher and MCP server load the repository-root `.env` explicitly.

## Run Locally

Start the API:

```powershell
.\scripts\run_api.ps1
```

Start the browser UI:

```powershell
.\scripts\run_web.ps1
```

Open `http://localhost:3000`.

## MCP Server

Run the stdio MCP server:

```powershell
.\scripts\run_mcp.ps1
```

Example local MCP client command:

```json
{
  "mcpServers": {
    "dbmap-postgres": {
      "command": "powershell",
      "args": ["-ExecutionPolicy", "Bypass", "-File", "C:\\Code\\AI Agents\\Database Agent\\scripts\\run_mcp.ps1"]
    }
  }
}
```

The MCP process reads the repository's local `.env`; credentials do not need to be duplicated in client configuration. See [MCP_AGENT_GUIDE.md](MCP_AGENT_GUIDE.md) for the recommended agent workflow, tool-selection guidance, safety rules, and example prompts.

## MCP Tools

- `database_connectivity_check`
- `database_graph_snapshot`
- `database_search`
- `database_neighbors`
- `database_explain_object`
- `database_readonly_query`

`database_readonly_query` accepts only guarded `SELECT` or `WITH` statements, applies a row limit, runs in a read-only transaction, and uses the configured statement timeout.

## Safe Read-only PostgreSQL Role

For an existing database, create a dedicated reader role:

```sql
create role dbmap_reader login password 'replace-with-a-strong-password';
grant connect on database your_database to dbmap_reader;
grant usage on schema public to dbmap_reader;
grant select on all tables in schema public to dbmap_reader;
alter default privileges in schema public grant select on tables to dbmap_reader;
```

Repeat the schema grants for every schema you want Database Agent to map.

## API

- `GET /health`
- `GET /graph`
- `GET /graph/search?q=customers`
- `GET /graph/node/{node_id}`
- `WS /graph/live`

## Tests

```powershell
python -m pytest services/dbmap/tests
```
