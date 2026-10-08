# Multi-user TableFox (public ChatGPT plugin)

Goal: one hosted TableFox that any ChatGPT user can install, sign in to, and point at their own PostgreSQL database. Self-hosted single-user mode stays unchanged; multi-user mode is enabled with `DBMAP_MULTI_TENANT=true`. No existing query safety control changes.

## Decisions

| Topic | Decision |
|---|---|
| Login | Auth0 (supports the dynamic client registration ChatGPT uses). TableFox only verifies tokens. |
| Write-capable DB roles | Refused at connect time. Users must supply a role without INSERT/UPDATE/DELETE/TRUNCATE/DDL privileges. |
| Hosting | Open. Prefer an always-on host (own VPS or Oracle Always Free); sleeping free tiers risk cold-start failures in review. Needed from phase 4. |
| Credential entry | Web page only (`/connect`), never through chat. |

## Phases

1. **Per-user services.** Registry mapping the token `sub` to a `DatabaseMapService` built from that user's stored credentials, with per-user cache, audit, and runtime paths. Idle eviction closes pools. Tools resolve the service from the caller's token. Baseline and context-file tools report unavailable in multi-user mode. Tests prove no cross-user leakage of DB, cache, audit, or context window.
2. **OAuth.** mcp 2 `TokenVerifier` checking the Auth0 JWT against its JWKS (signature, issuer, audience, expiry). Missing or invalid token gets 401 with the resource-metadata pointer.
3. **Credential store and `/connect`.** Encrypted at rest (key from an environment secret). Test before saving, replace, delete. SSRF guard on every connect: resolve the host and reject loopback, private, link-local, and metadata addresses; port 5432 by default; `sslmode=require` minimum. Write-privilege check refuses non-reader roles. Credentials never appear in logs, audit events, errors, or tool output.
4. **Hosting.** Docker image, HTTPS, app database for users and credentials, per-user rate and concurrency limits, health endpoint.
5. **Submission.** Verified domain (`/.well-known/openai-apps-challenge`), privacy policy (disclose credential storage and deletion), terms, 64 px icon under 5 KB, descriptions, screenshots, reviewer account on a read-only hrcore demo, 5 positive and 3 negative test prompts, then submit through the OpenAI plugin portal ("With MCP").
