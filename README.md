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
examples/postgres     Sample PostgreSQL database
scripts               Local PowerShell helpers
```

## Setup

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

## Sample PostgreSQL Database

Start the local sample database:

```powershell
.\scripts\run_sample_postgres.ps1
```

Use this demo connection string in `.env`:

```text
DATABASE_URL=postgresql://dbmap_reader:change-me-demo-only@localhost:55432/dbmap_demo
```

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
      "args": ["-ExecutionPolicy", "Bypass", "-File", "C:\\Code\\AI Agents\\Database Agent\\scripts\\run_mcp.ps1"],
      "env": {
        "DATABASE_URL": "postgresql://dbmap_reader:change-me-demo-only@localhost:55432/dbmap_demo"
      }
    }
  }
}
```

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
