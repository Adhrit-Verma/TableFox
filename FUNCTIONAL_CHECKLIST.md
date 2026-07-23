# TableFox Functional Checklist

Use this checklist before a release or after changing database, graph, MCP, API, or UI behavior.

## Purpose

TableFox exists to help AI agents and humans understand an unfamiliar PostgreSQL database without repeatedly loading the entire schema. It must support focused discovery, trustworthy relationship navigation, guarded read-only validation, and a clear local visual map. Its production goal is to provide evidence-backed database context, not to replace SDE or DBA judgment.

## Automated Gate

Stop running development services before the production web build.

- [ ] `python -m pytest -q` passes.
- [ ] `python -m ruff check services scripts` passes.
- [ ] `python -m pip check` reports no broken requirements.
- [ ] `npm run web:build` completes without type, lint, or build errors.
- [ ] `git diff --check` reports no whitespace errors.
- [ ] `git status --short` does not list `.env`, credentials, cache files, or build output.

## PostgreSQL Connection And Safety

- [ ] `.\run.cmd -Check` connects to the database named in `.env`.
- [ ] The reported database and user are the intended production-safe values.
- [ ] The role is read-only by default, or the launcher prints the expected warning.
- [ ] Metadata discovery works without write privileges.
- [ ] A simple `SELECT` succeeds through `database_readonly_query`.
- [ ] `INSERT`, `UPDATE`, `DELETE`, DDL, and multiple SQL statements are rejected.
- [ ] A caller-supplied large `LIMIT` is still capped by `DBMAP_MAX_QUERY_ROWS`.
- [ ] A slow query is stopped by `DBMAP_STATEMENT_TIMEOUT_MS`.

## Graph Fidelity

- [ ] The snapshot contains every permitted user schema.
- [ ] Tables, views, materialized views, columns, primary keys, foreign keys, constraints, and indexes are represented.
- [ ] Foreign-key direction matches the PostgreSQL definition.
- [ ] Stable IDs distinguish objects with the same name in different schemas.
- [ ] Schema filtering excludes unselected schemas.
- [ ] Bounded snapshots preserve schema and table context instead of returning orphaned columns.
- [ ] Refreshing after a schema change returns the new structure.

## AI Agent Workflow

Run this sequence through a real MCP client.

- [ ] `database_connectivity_check` identifies the expected database safely.
- [ ] `database_search` finds a known business table from its name, column, or comment.
- [ ] The first exact table-name match ranks ahead of partial column matches.
- [ ] `database_explain_object` returns columns and inbound/outbound relationships for the selected stable ID.
- [ ] `database_neighbors` at depth 1 returns a small, connected subgraph.
- [ ] `database_graph_snapshot` respects schema and node limits.
- [ ] `database_readonly_query` validates one focused assumption without returning excessive rows.
- [ ] The agent can answer a join-path question without requesting the full database graph.

## Production Impact Roadmap

These checks apply when the corresponding capability is implemented. Do not mark them complete based only on catalog metadata.

### Semantic Context

- [x] Table and column comments are included in search and object explanations.
- [x] Approved business documentation can be linked to the relevant schema objects with a source and last-updated date.
- [x] ORM models or migration history are linked only when their database identity and schema fingerprint match the connected database.
- [x] The agent can identify a verified source-of-truth table or state the evidence and uncertainty.
- [x] Object explanations expose catalog evidence and state when source-of-truth status is unverified.

### Usage-Aware Ranking

- [x] Ranking can use approved aggregate scan activity, ownership context, analyze freshness, and size signals.
- [ ] Join-frequency ranking is available only after an approved aggregate source is configured.
- [x] A search result explains which ranking signals affected its position.
- [x] Missing or stale usage telemetry is displayed as unavailable rather than inferred.
- [x] Usage data is aggregated, opt-in, and does not expose raw production query text.

### Safe Task Workflows

- [x] Workflows answer source of truth, verified join path, slow-report planning, and migration blast radius.
- [x] Workflows return stable IDs, evidence, unresolved ambiguity, and bounded results or next steps.
- [x] Multi-relation data queries are blocked unless catalog relationships connect every planned relation or an administrator approves the exception.

### SQL Verification

- [x] Query validation can run `EXPLAIN` without `ANALYZE` or result-row execution.
- [x] Cost, scan, estimated-row, lock-timeout, and statement-timeout policy is reported.
- [x] Common credential and PII-like result column names are blocked before rows are returned.
- [x] Approved context classifications augment name checks, and restricted schemas are enforced before metadata caching and row access.
- [x] An authenticated API administrator must approve non-schema exceptions outside the configured low-risk policy; MCP cannot self-approve.

### Schema Change Impact

- [x] Snapshot comparison identifies added, removed, and changed schemas, relations, columns, constraints, indexes, and edges.
- [x] A change report identifies dependent views, foreign keys, documented consumers, and known saved queries.
- [x] The report distinguishes confirmed dependencies from inferred relationships.
- [x] The schema-impact GitHub workflow links the report artifact to the migration path under review.

### Developer And DBA Integration

- [x] MCP, command-line, CI, and HTTP workflows expose the same stable IDs and safety rules.
- [x] A migration review command runs from catalog metadata without accessing application rows.
- [x] Owners can configure allowed schemas, restricted schemas, read-only policies, telemetry access, and audit retention.
- [x] Operators can audit who requested metadata, explanations, workflows, and data queries without logging SQL text or rows.
- [x] HTTP users authenticate with hashed API keys and receive role-scoped authorization.

## HTTP And Live Updates

- [ ] `GET /health` returns HTTP 200 when PostgreSQL is reachable.
- [ ] `GET /health` returns HTTP 503 without exposing connection details when PostgreSQL is unavailable.
- [ ] `GET /graph?max_nodes=5` returns between 1 and 5 coherent nodes.
- [ ] `GET /graph/search?q=<known-term>` returns relevant ranked results.
- [ ] `GET /graph/node/{stable-id}` returns the expected object explanation.
- [ ] `WS /graph/live` sends a graph snapshot, then heartbeats when the structure is unchanged.
- [ ] The Next.js `/dbmap-api` proxy reaches both HTTP and WebSocket endpoints.

## Human Visual Map

- [ ] `.\run.cmd` opens `http://127.0.0.1:3000` with styled content and a loaded graph.
- [ ] Search opens relevant results and closes by result selection, close button, Escape, and outside click.
- [ ] Schema filters update visible nodes without showing excluded schemas.
- [ ] Selecting a table highlights its relationships and opens the correct inspector details.
- [ ] **Show columns** is disabled until a table or view is selected.
- [ ] **Show columns** displays readable column nodes, dashed table links, and the visible column count.
- [ ] Zoom in, zoom out, fit graph, and focus selection work without layout jumps.
- [ ] Closing the inspector clears selected-column nodes.
- [ ] Restarting FastAPI reconnects live updates without reloading the UI.
- [ ] Desktop and narrow layouts have no clipped controls, overlapping text, or inaccessible actions.

## SOLID Boundary Check

- [x] FastAPI and MCP modules contain transport concerns only.
- [x] Shared graph/search/navigation/query rules live in `DatabaseMapService` once.
- [x] PostgreSQL catalog, guarded execution, and cache behavior stay in `PostgresIntrospector`.
- [x] Graph construction and traversal stay in `GraphEngine`.
- [x] Query plan assessment and sensitive-column classification stay in pure policy functions.
- [x] Adding a transport does not require duplicating application rules.
- [x] New abstractions are added only when a second real implementation or repeated rule requires them.

## Release Result

- [ ] Record the database identity, test date, tester, failed checks, accepted risks, and follow-up issue links.
- [ ] Do not publish while any safety, graph-fidelity, MCP workflow, or credential check is failing.

### Verification Record: 2026-07-22

- Tester: Codex automated and live smoke checks.
- Database: `aviatrac_elog` as `appuser`; 64 tables, 4 views, 1,545 graph nodes.
- Passed: 30 Python tests, Ruff, dependency check, compile check, whitespace check, and Next.js production build.
- Passed live: connectivity, refreshed graph snapshot, HTTP health, HTTP `POST /query/explain`, MCP `database_explain_query`, and sensitive-column blocking.
- Blocking risk: `appuser` is not read-only by default. Use a dedicated least-privilege reader before production use.
- Completed roadmap slice: semantic documents, version-gated code links, aggregate usage telemetry, policy classification, restricted schemas, workflows, schema diff, audit logging, API roles, and CI.
- Remaining limitation: join-frequency telemetry stays unavailable until an approved aggregate source exists; raw query logs are intentionally unsupported.
- Publish status: not approved until the database role issue is resolved and the remaining mandatory release checks are run in the target environment.

### Verification Record: 2026-07-23

- Tester: Codex automated, live PostgreSQL, HTTP/MCP, and browser smoke checks.
- Passed: 41 Python tests, Ruff, dependency and compile checks, whitespace check, and Next.js production build.
- Passed live: refreshed graph, six view-dependency edges, aggregate statistics for 64 relations, non-executing two-relation planning, declared join validation, audit redaction, and unchanged baseline comparison.
- Passed browser: graph load, live connection, responsive layout, search open/clear behavior, and zero console errors.
- Security: hashed API-key authentication and roles are covered end to end; restricted schemas cannot be administrator-overridden; temporary test keys were removed.
- Remaining limitation: approved join-frequency telemetry is not configured and raw production query logs remain intentionally unsupported.
- Blocking risk: the configured `appuser` role is still not read-only by default. Production publication remains blocked until a dedicated reader is used.
