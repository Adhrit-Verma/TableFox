# Multi-user TableFox (public ChatGPT plugin)

Goal: one hosted TableFox that any ChatGPT user can install, sign in to, and point at their own PostgreSQL database. Self-hosted single-user mode stays unchanged; multi-user mode is enabled with `DBMAP_MULTI_TENANT=true`. No existing query safety control changes.

## Decisions

| Topic | Decision |
|---|---|
| Login | Auth0 (supports the dynamic client registration ChatGPT uses). TableFox only verifies tokens. |
| Write-capable DB roles | Refused at connect time. Users must supply a role without INSERT/UPDATE/DELETE/TRUNCATE/DDL privileges. |
| Hosting | Open. Prefer an always-on host (own VPS or Oracle Always Free); sleeping free tiers risk cold-start failures in review. Needed from phase 4. |
| Credential entry | Web page only (`/connect`), never through chat. |

## Status

Phases 1-3 are built (`tenants.py`, `mcp_server.py`, `tests/test_tenants.py`) and a container image is defined (`services/dbmap/Dockerfile`). Remaining: Auth0 setup, hosting, submission assets.

How a user connects: the first tool call without a saved database returns a private, signed, single-use link (10 minutes) to `/connect`. The user pastes a read-only `postgresql://` URL there; TableFox vets the address, connects once over SSL to refuse write-capable roles, and stores the URL encrypted. `database_connection_link` issues a new link to replace or remove it.

## Hosted configuration

| Variable | Value |
|---|---|
| `DBMAP_MULTI_TENANT` | `true` (set in the image) |
| `DBMAP_PUBLIC_URL` | `https://<your-host>` |
| `DBMAP_OAUTH_ISSUER` | Auth0 issuer, e.g. `https://<tenant>.us.auth0.com/` |
| `DBMAP_OAUTH_AUDIENCE` | Auth0 API identifier; defaults to `https://<your-host>/mcp` |
| `DBMAP_TENANT_KEY` | Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Losing it makes saved connections unreadable. |
| `DBMAP_TENANT_PORTS` | Allowed database ports, default `5432` |

The image keeps state in `/data` (encrypted credentials, per-user cache and audit). Mount a volume there. `/health` answers `{"status": "ok"}`.

Known limits: no per-user request rate limit yet (each user is bounded by 2 pooled connections and the statement timeout); connect links appear in HTTP access logs until used or expired.

## Phases

1. **Per-user services.** Registry mapping the token `sub` to a `DatabaseMapService` built from that user's stored credentials, with per-user cache, audit, and runtime paths. Idle eviction closes pools. Tools resolve the service from the caller's token. Baseline and context-file tools report unavailable in multi-user mode. Tests prove no cross-user leakage of DB, cache, audit, or context window.
2. **OAuth.** mcp 2 `TokenVerifier` checking the Auth0 JWT against its JWKS (signature, issuer, audience, expiry). Missing or invalid token gets 401 with the resource-metadata pointer.
3. **Credential store and `/connect`.** Encrypted at rest (key from an environment secret). Test before saving, replace, delete. SSRF guard on every connect: resolve the host and reject loopback, private, link-local, and metadata addresses; port 5432 by default; `sslmode=require` minimum. Write-privilege check refuses non-reader roles. Credentials never appear in logs, audit events, errors, or tool output.
4. **Hosting.** Docker image, HTTPS, app database for users and credentials, per-user rate and concurrency limits, health endpoint.
5. **Submission.** Verified domain (`/.well-known/openai-apps-challenge`), privacy policy (disclose credential storage and deletion), terms, 64 px icon under 5 KB, descriptions, screenshots, reviewer account on a read-only hrcore demo, 5 positive and 3 negative test prompts, then submit through the OpenAI plugin portal ("With MCP").
