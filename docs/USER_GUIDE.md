# TableFox user guide

TableFox lets an AI assistant answer questions about your PostgreSQL database. You ask in plain English ("which products sold best last month?"), and TableFox finds the few tables that matter and runs safe, read-only queries. It can never change your data.

It works with any assistant that supports MCP connectors: ChatGPT, Claude, and coding agents such as Claude Code, Codex, Cursor and VS Code. Setup for each is in [AGENTS.md](AGENTS.md).

---

## 1. Add TableFox to your assistant

The hosted TableFox address is:

```
https://tablefox.onrender.com/mcp
```

**ChatGPT**
1. Settings → Security and login → turn on **Developer mode**.
2. Go to chatgpt.com/plugins → **+** → add a custom MCP server with the address above and **OAuth** sign-in.
3. Sign in (or sign up) when the TableFox login page opens.

**Claude (claude.ai or Claude Desktop)**
1. Settings → **Connectors** → **Add custom connector**.
2. Paste the address above and sign in when asked.

**Coding agents** (Claude Code, Codex, Cursor, VS Code, Gemini CLI, Windsurf): see [AGENTS.md](AGENTS.md).

## 2. Connect a database

Ask your assistant anything, for example *"What tables are in my database?"*. The first time, TableFox replies with a **private link** (it works once and expires after 10 minutes). Open it and fill in:

| Field | Example |
|---|---|
| Name | `sales`: how you'll refer to this database |
| Host | `db.example.com` |
| Port | `5432` |
| Database | `postgres` |
| User | `tablefox_reader`: a **read-only** user (see below) |
| Password | that user's password |
| SSL | Require |

Click **Test and connect**. TableFox checks the connection and saves it encrypted. Your assistant never sees these details.

> Never paste database passwords into the chat. Always use the private link.

### Create a read-only user

TableFox only accepts users that cannot write. Run this once as your database administrator (replace the `<placeholders>`):

```sql
create role tablefox_reader login password '<long random password, letters and digits>';
alter role tablefox_reader set default_transaction_read_only = on;
grant connect on database <your_database> to tablefox_reader;
grant usage on schema public to tablefox_reader;
grant select on all tables in schema public to tablefox_reader;
alter default privileges for role <table_owner> in schema public grant select on tables to tablefox_reader;
-- PostgreSQL 14 or older only (keep your app able to create tables):
-- grant create on schema public to <table_owner>;
-- revoke create on schema public from public;
```

Hosted databases:
- **Neon:** run it in the project's SQL Editor; use the host from **Connect**. The default owner is `neondb_owner`, so use that in place of `<table_owner>`.
- **Supabase / AWS RDS / others:** same SQL. Make sure the database accepts connections from the internet over SSL.

If TableFox says a user *"is not read-only"*, check the user name it names. You probably used the owner or admin account by mistake.

## 3. Ask questions

Good questions are specific:
- *Which 5 products earned the most revenue?*
- *How many orders were cancelled per country this year?*
- *Which customers signed up but never ordered?*

TableFox will **refuse** anything that changes data (*"delete…"*, *"update…"*, *"create table…"*), and it hides personal-data columns such as emails and passwords.

## 4. Several databases

Add more databases with the same private link (ask *"give me the TableFox link to manage my databases"*). Give each a short name. When a question doesn't say which one, the assistant will ask. Answer with the name (*"use sales"*).

## Troubleshooting

| You see | Do this |
|---|---|
| "This link has expired or is incomplete" | Ask for a new link; each works once for 10 minutes. |
| "'…' is not read-only" | Connect with your read-only user, not the owner/admin. |
| "The server does not accept SSL connections" | Enable SSL on your database; TableFox requires it. |
| "The database host must be a public internet address" | TableFox can't reach private/internal addresses. |
| "Wrong user or password" | Check the read-only user's password. |
| The assistant uses old tool names or can't find a TableFox tool | Refresh or reconnect TableFox in your assistant's connector settings, then start a new chat. |

## Privacy

TableFox stores your saved connections (encrypted), your database's table and column names, and an audit log without SQL text or results. It never stores your rows. Full details: [tablefox.onrender.com/privacy](https://tablefox.onrender.com/privacy).
