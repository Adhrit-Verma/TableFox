<div align="center">

# 🧭 SchemaPilot

### Local PostgreSQL schema intelligence for AI agents, developers, and database explorers.

<img src="https://media.giphy.com/media/9LATKVrWXmlIKovXkE/giphy.gif" width="420" alt="AI robot assistant animation from GIPHY" />

<br />

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Read--Only-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-UI-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-Agent%20Tools-7C3AED?style=for-the-badge)
![Local First](https://img.shields.io/badge/Local--First-Safe%20by%20Default-22C55E?style=for-the-badge)

</div>

---

## ✨ What is SchemaPilot?

**SchemaPilot** is a local-first PostgreSQL mapping tool that turns your database structure into a searchable, agent-friendly graph.

It helps both **humans** and **AI agents** understand a database quickly by mapping:

- schemas
- tables and views
- columns
- indexes
- constraints
- primary keys
- foreign keys
- object relationships

It includes a **Python graph engine**, a **FastAPI backend**, an **MCP stdio server**, and a **Next.js browser UI** with an interactive 2D schema map.

---

## 🧠 Why I Built This

When an AI agent works with a database, it should not blindly guess table names or relationships.

SchemaPilot gives the agent a safe map first.

```text
Without SchemaPilot:
Agent guesses tables → writes risky SQL → slow debugging

With SchemaPilot:
Agent checks schema graph → finds relationships → runs safe read-only queries
```

This makes database agents more accurate, safer, and easier to debug.

---

## 🚀 Core Features

| Feature | What it does |
|---|---|
| 🔐 Read-only database access | Uses safe PostgreSQL credentials by default |
| 🧩 Schema graph builder | Maps tables, views, columns, indexes, constraints, and foreign keys |
| 🤖 MCP tools for AI agents | Lets tools like Claude/Cursor-style clients search and traverse schema context |
| 🌐 FastAPI local API | Exposes graph, search, node details, health, and live refresh endpoints |
| 🕸️ Interactive Next.js UI | Browser-based Cytoscape 2D graph with filters and inspector panel |
| 🔍 Search and traversal | Quickly find tables, columns, and related objects |
| 🛡️ Guarded SQL execution | Allows only protected `SELECT` / `WITH` read-only queries |
| ⚡ One-command launcher | Starts backend + frontend from a single local command |

---

## 🖼️ Visual Idea

<div align="center">

<img src="https://media.giphy.com/media/jLrRvwKSPsf7urOUDq/giphy.gif" width="360" alt="AI work smarter robot animation from GIPHY" />

</div>

```text
PostgreSQL Database
       ↓
SchemaPilot Graph Engine
       ↓
FastAPI + WebSocket API
       ↓
Next.js Interactive Map
       ↓
MCP Tools for AI Agents
```

---

## 🏗️ Project Structure

```text
apps/web              Next.js local browser UI
services/dbmap        Python graph engine, FastAPI API, MCP server
scripts               Local PowerShell helpers
run.cmd               One-command launcher
.env.example          Environment template
MCP_AGENT_GUIDE.md    Agent workflow and safety guide
```

---

## ⚡ One-command Start

For a database already configured in `.env`:

```powershell
.\run.cmd
```

This will:

```text
1. Validate your database connection
2. Build the schema graph
3. Start the FastAPI backend
4. Start the Next.js web UI
5. Open http://localhost:3000
```

Press `Ctrl + C` to stop the services started by the launcher.

---

## 🛠️ First-time Installation

Configure `.env`, then run:

```powershell
.\run.cmd -Install
```

Useful launcher modes:

```powershell
.\run.cmd -Check       # Validate credentials and build graph, then exit
.\run.cmd -NoBrowser   # Start services without opening the browser
```

The first install can take a few minutes. After that, normal usage is usually just:

```powershell
.\run.cmd
```

---

## 🔧 Manual Setup

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

Copy the environment file:

```powershell
Copy-Item .env.example .env
```

Real credentials should stay inside `.env`. Do not commit `.env` to Git.

---

## 🔑 Environment Configuration

Use either `DATABASE_URL` or individual PostgreSQL variables.

### Option 1: DATABASE_URL

```env
DATABASE_URL=postgresql://dbmap_reader:password@localhost:5432/your_database
```

### Option 2: Individual PG variables

```env
PGHOST=localhost
PGPORT=5432
PGDATABASE=your_database
PGUSER=dbmap_reader
PGPASSWORD=replace-with-a-strong-password
```

If `DATABASE_URL` is present, it takes priority over the individual `PG*` variables.

---

## 🧪 Run Locally

Start the API:

```powershell
.\scripts\run_api.ps1
```

Start the browser UI:

```powershell
.\scripts\run_web.ps1
```

Open:

```text
http://localhost:3000
```

---

## 🤖 MCP Server

Start the stdio MCP server:

```powershell
.\scripts\run_mcp.ps1
```

Example local MCP client configuration:

```json
{
  "mcpServers": {
    "schemapilot-postgres": {
      "command": "powershell",
      "args": [
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:\\Code\\AI Agents\\SchemaPilot\\scripts\\run_mcp.ps1"
      ]
    }
  }
}
```

The MCP process loads the repository-root `.env`, so database credentials do not need to be duplicated in the client configuration.

For workflow guidance, see:

```text
MCP_AGENT_GUIDE.md
```

---

## 🧰 MCP Tools

| Tool | Purpose |
|---|---|
| `database_connectivity_check` | Tests database access and validates configuration |
| `database_graph_snapshot` | Returns the current schema graph summary |
| `database_search` | Searches schemas, tables, columns, views, and related objects |
| `database_neighbors` | Finds nearby connected graph nodes |
| `database_explain_object` | Explains a selected table, column, view, or constraint |
| `database_readonly_query` | Runs guarded read-only SQL queries |

`database_readonly_query` only accepts protected `SELECT` or `WITH` statements, applies a row limit, runs inside a read-only transaction, and uses the configured statement timeout.

---

## 🔐 Safe Read-only PostgreSQL Role

For an existing database, create a dedicated reader role:

```sql
create role dbmap_reader login password 'replace-with-a-strong-password';
grant connect on database your_database to dbmap_reader;
grant usage on schema public to dbmap_reader;
grant select on all tables in schema public to dbmap_reader;
alter default privileges in schema public grant select on tables to dbmap_reader;
```

Repeat schema grants for every schema you want SchemaPilot to map.

---

## 🌐 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | API health check |
| `GET` | `/graph` | Full graph response |
| `GET` | `/graph/search?q=customers` | Search graph objects |
| `GET` | `/graph/node/{node_id}` | Inspect one graph node |
| `WS` | `/graph/live` | Live graph/status stream |

---

## 🧑‍💻 Example Agent Prompts

Use prompts like these with an MCP client:

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

## ✅ Tests

Run backend tests:

```powershell
python -m pytest services/dbmap/tests
```

---

## 🗺️ Roadmap

- [ ] Add graph export as JSON
- [ ] Add Mermaid ER diagram export
- [ ] Add schema comparison between two database snapshots
- [ ] Add saved graph history
- [ ] Add AI-generated table documentation
- [ ] Add Docker Compose setup
- [ ] Add role-based UI access
- [ ] Add query explanation mode
- [ ] Add PostgreSQL performance hints for indexes and constraints

---

## 🎯 Resume Line

> Built **SchemaPilot**, a local-first PostgreSQL schema intelligence tool using Python, FastAPI, Next.js, Cytoscape, and MCP to help AI agents safely search, traverse, explain, and query relational database structures through guarded read-only workflows.

---

## ⚠️ Safety Notes

SchemaPilot is designed for local and read-only usage by default.

Recommended safety practices:

```text
Use a dedicated read-only PostgreSQL role
Never commit .env files
Avoid production credentials for demos
Use local databases while testing
Review generated SQL before trusting outputs
```

---

## 🙌 Built For

```text
AI agent builders
Backend developers
Database-heavy projects
MCP experiments
PostgreSQL exploration
Schema debugging
Local-first developer tooling
```

---

<div align="center">

### 🧭 SchemaPilot

**Give your AI agent a database map before it starts flying.**

</div>