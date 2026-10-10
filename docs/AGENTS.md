# Use TableFox with any AI assistant or coding agent

TableFox is a standard [MCP](https://modelcontextprotocol.io) server, so any MCP-capable assistant can use it. Pick one of two ways to run it:

| | Hosted | Self-hosted |
|---|---|---|
| Address | `https://tablefox.onrender.com/mcp` | runs on your machine (stdio) |
| Sign-in | OAuth (sign up once in the browser) | none |
| Database details | saved encrypted on TableFox's private connect page | your own `.env` file |
| Several databases | yes, by name | one per `.env` |
| Best for | everyday use, ChatGPT / Claude apps | local development, private networks, CI |

> The hosted sign-in has been tested end to end with ChatGPT. The other clients below use the same standard MCP OAuth flow (discovery + dynamic client registration). If one fails to sign in, use the self-hosted setup and [open an issue](https://github.com/Adhrit-Verma/TableFox/issues).

After connecting, the first question returns a private link to connect a database; see the [user guide](USER_GUIDE.md#2-connect-a-database).

---

## Hosted setup

### Claude Code
```bash
claude mcp add --transport http tablefox https://tablefox.onrender.com/mcp
```
Then run `/mcp` inside Claude Code, choose **tablefox**, and **Authenticate**.

### Codex (CLI and IDE extension)
```bash
codex mcp add tablefox --url https://tablefox.onrender.com/mcp
codex mcp login tablefox
```
This writes to `~/.codex/config.toml`:
```toml
[mcp_servers.tablefox]
url = "https://tablefox.onrender.com/mcp"
```

### Cursor
`~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):
```json
{
  "mcpServers": {
    "tablefox": { "url": "https://tablefox.onrender.com/mcp" }
  }
}
```
Open **Settings → MCP**, then click **Connect** next to tablefox to sign in.

### VS Code (GitHub Copilot agent mode)
`.vscode/mcp.json`. The root key is `servers`, and `type` is required:
```json
{
  "servers": {
    "tablefox": { "type": "http", "url": "https://tablefox.onrender.com/mcp" }
  }
}
```
VS Code asks you to sign in the first time a TableFox tool runs.

### Gemini CLI
`~/.gemini/settings.json`. Use `httpUrl`; `url` means the older SSE transport:
```json
{
  "mcpServers": {
    "tablefox": { "httpUrl": "https://tablefox.onrender.com/mcp" }
  }
}
```

### Windsurf
`~/.codeium/windsurf/mcp_config.json`:
```json
{
  "mcpServers": {
    "tablefox": { "serverUrl": "https://tablefox.onrender.com/mcp" }
  }
}
```
Click refresh in the MCP panel after saving.

### ChatGPT and Claude apps
See the [user guide](USER_GUIDE.md#1-add-tablefox-to-your-assistant).

---

## Self-hosted setup

1. Install (Python 3.11+):
   ```bash
   git clone https://github.com/Adhrit-Verma/TableFox.git
   cd TableFox
   python -m venv .venv
   .venv/bin/pip install -e services/dbmap        # Windows: .venv\Scripts\pip install -e services/dbmap
   ```
2. Create `.env` in the repo root (it is git-ignored) with a **read-only** user ([how](USER_GUIDE.md#create-a-read-only-user)):
   ```
   DATABASE_URL=postgresql://tablefox_reader:<password>@db.example.com:5432/postgres?sslmode=require
   ```
3. Point your client at the `dbmap-mcp` command. Use absolute paths; replace `/path/to/TableFox` with yours. On Windows the command is `C:\\path\\to\\TableFox\\.venv\\Scripts\\dbmap-mcp.exe`.

**Claude Code**
```bash
claude mcp add tablefox --env DBMAP_ENV_FILE=/path/to/TableFox/.env -- /path/to/TableFox/.venv/bin/dbmap-mcp
```

**Codex**
```bash
codex mcp add tablefox --env DBMAP_ENV_FILE=/path/to/TableFox/.env -- /path/to/TableFox/.venv/bin/dbmap-mcp
```

**Cursor, Gemini CLI, Windsurf, Claude Desktop** (`mcpServers` JSON):
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

**VS Code** (`.vscode/mcp.json`):
```json
{
  "servers": {
    "tablefox": {
      "type": "stdio",
      "command": "/path/to/TableFox/.venv/bin/dbmap-mcp",
      "env": { "DBMAP_ENV_FILE": "/path/to/TableFox/.env" }
    }
  }
}
```

Restart the client after editing `.env`; a running stdio server keeps its settings.

---

## Tell your agent how to use it

Add this to your project's agent instructions (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, or Copilot instructions):

```markdown
## Database (TableFox MCP)
- For any question about the database, call `database_task_context` with the full question first.
- Then answer with at most five bounded SELECT/WITH statements in one `database_readonly_batch` call,
  using only the joins it returned.
- TableFox is read-only. Never attempt writes or DDL, and never ask for database credentials in chat.
- If several databases are connected, pass the right name as the `database` argument (ask if unclear).
```

The full tool reference, workflow and patterns for agents are in [MCP_AGENT_GUIDE.md](../MCP_AGENT_GUIDE.md).
