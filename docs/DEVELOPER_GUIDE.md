# TableFox developer guide

How TableFox is built, how to run and test it, and how to host your own multi-user instance. For using TableFox, see the [user guide](USER_GUIDE.md); for connecting assistants, see [AGENTS.md](AGENTS.md).

## How it works

```
Assistant (ChatGPT, Claude, Codex, …)
   │  MCP over HTTP + OAuth (hosted)  or  stdio (self-hosted)
   ▼
mcp_server.py ── tools, /connect page, /, /privacy, /terms
   │
   ├─ tenants.py   (hosted only) per-user services, encrypted connection store, address vetting
   ▼
service.py ─── DatabaseMapService: every use case, shared by MCP and the HTTP API
   ├─ retrieval.py   ranks relations for a question (BM25F + fuzzy) → small context pack
   ├─ graph.py       schema graph, join paths (pure, no I/O)
   ├─ readonly.py    SELECT/WITH-only validator
   ├─ query_policy.py EXPLAIN cost/row policy, sensitive-column rules
   └─ postgres.py    connection pool, catalog introspection, read-only transactions
```

A question normally takes two calls: `database_task_context` (which tables and joins matter) and `database_readonly_batch` (up to five guarded SELECTs in one read-only transaction).

## Repository layout

| Path | What |
|---|---|
| `services/dbmap/src/dbmap/` | the Python package (MCP server, API, service, safety layers) |
| `services/dbmap/src/dbmap/static/` | landing page and icon served by the hosted server |
| `services/dbmap/tests/` | unit tests (no database needed) |
| `services/dbmap/Dockerfile`, `render.yaml` | hosted deployment |
| `apps/web/` | local visual graph explorer (Next.js) |
| `scripts/` | dev launcher, benchmarks, `demo_shop.sql` demo database |

## Run locally

```bash
python -m venv .venv
.venv/bin/pip install -e "services/dbmap[dev]"     # Windows: .venv\Scripts\pip ...
cp .env.example .env                                 # then set a read-only DATABASE_URL or PG* values
.venv/bin/dbmap-mcp                                  # MCP over stdio
```

Windows shortcuts: `.\run.cmd -Install`, `.\run.cmd` (API + web UI), `.\run.cmd -Check`.

To try the hosted (multi-user) mode locally you need an OAuth issuer; see [Host your own](#host-your-own-multi-user-instance).

## Tests and checks

```bash
python -m pytest -q
python -m ruff check services scripts
python -m pip check
```

CI (`.github/workflows/quality.yml`) runs the same three on every push, plus the web build. Tests use fakes and never need a database. For a real end-to-end check, load [`scripts/demo_shop.sql`](../scripts/demo_shop.sql) into a throwaway PostgreSQL and connect its `tablefox_reader` user.

## Configuration

Set in `.env` (or the file named by `DBMAP_ENV_FILE`). The file overrides process variables.

**Database (self-hosted)**

| Variable | Meaning |
|---|---|
| `DATABASE_URL` or `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` | the database; `DATABASE_URL` wins |
| `DBMAP_STATEMENT_TIMEOUT_MS` | per-statement timeout (default 5000) |
| `DBMAP_MAX_QUERY_ROWS` | row cap per query (default 200) |
| `DBMAP_MAX_EXPLAIN_COST`, `DBMAP_MAX_EXPLAIN_ROWS` | plan ceilings before a query is refused |
| `DBMAP_ALLOWED_SCHEMAS`, `DBMAP_RESTRICTED_SCHEMAS` | schema allow/deny lists |
| `DBMAP_ALLOW_SENSITIVE_DATA` | `false` by default; blocks credential/PII-like columns |
| `DBMAP_CONTEXT_WINDOW` | relations remembered as already sent (0 = off) |

**MCP transport**

| Variable | Meaning |
|---|---|
| `DBMAP_MCP_TRANSPORT` | `stdio` (default) or `streamable-http` |
| `DBMAP_MCP_PORT` / `PORT` | HTTP port (default 8765; hosts like Render set `PORT`) |
| `DBMAP_MCP_BIND` | bind address (default `127.0.0.1`; containers use `0.0.0.0`) |
| `DBMAP_MCP_PUBLIC_HOST` | public host name allowed in the `Host` header (single-user HTTP) |

**Hosted multi-user mode**

| Variable | Meaning |
|---|---|
| `DBMAP_MULTI_TENANT` | `true` turns it on (set in the Docker image) |
| `DBMAP_PUBLIC_URL` | `https://your-host` |
| `DBMAP_OAUTH_ISSUER` | OAuth issuer, e.g. `https://tenant.us.auth0.com/` |
| `DBMAP_OAUTH_AUDIENCE` | token audience; defaults to `<public url>/mcp` |
| `DBMAP_TENANT_KEY` | Fernet key encrypting saved connections; losing it makes them unreadable |
| `DBMAP_TENANT_DB` | where connections are stored: file path or `postgresql://` URL (required on hosts without a disk) |
| `DBMAP_TENANT_PORTS` | allowed database ports (default `5432`) |

## Host your own multi-user instance

1. **Store:** a small PostgreSQL for saved connections (e.g. Neon free tier) → `DBMAP_TENANT_DB`.
2. **OAuth:** an issuer that supports dynamic client registration (e.g. Auth0). Create an API whose identifier is `https://your-host/mcp`; set it as the tenant's default audience; enable dynamic client registration; allow third-party apps user-delegated access to the API; promote your login connections to domain level.
3. **Key:** `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` → `DBMAP_TENANT_KEY`.
4. **Deploy:** Render reads `render.yaml` (Docker, health check `/health`, secret prompts). Any container host works: build `services/dbmap/Dockerfile`, set the variables above, expose the port behind HTTPS.
5. **Check:** `curl https://your-host/health` returns `{"status":"ok"}`; `POST /mcp` without a token returns `401` with a `WWW-Authenticate` header.

## Safety rules (do not weaken)

- Only `SELECT`/`WITH`; everything else is rejected before connecting (`readonly.py`).
- Every query runs in a read-only transaction with statement and lock timeouts, after an `EXPLAIN` policy check.
- Multi-relation queries must be connected by declared foreign keys or catalog dependencies.
- Credential/PII-like columns are blocked unless an operator opts in.
- Hosted mode: only read-only database users are accepted; connections must use SSL and resolve to public addresses (pinned with `hostaddr`, so DNS changes cannot redirect them); credentials are entered on `/connect`, never in chat, and stored encrypted.
- Audit events record actor, action and a SQL hash, never SQL text or rows.

Changes to `postgres.py`, `query_policy.py`, `readonly.py`, `security.py` or `tenants.py` need tests that prove these still hold.

## Contributing

1. Fork, branch, and keep changes focused.
2. Add or update tests; run the three checks above.
3. Open a pull request describing the behaviour change.

Licensed under [MIT](../LICENSE).
