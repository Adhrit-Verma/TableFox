"""Server-rendered pages for hosted TableFox (connect, done, invalid link).

No scripts and no external assets, so the strict Content-Security-Policy holds.
"""

from __future__ import annotations

from html import escape

PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'self'",
}

READER_SQL = """create role tablefox_reader login password '<long-random-password>';
grant connect on database <your_database> to tablefox_reader;
grant usage on schema public to tablefox_reader;
grant select on all tables in schema public to tablefox_reader;
alter default privileges for role <table_owner> in schema public
  grant select on tables to tablefox_reader;
-- PostgreSQL 14 or older only:
revoke create on schema public from public;"""

STYLE = """
:root {
  --bg: oklch(0.985 0.003 40); --surface: oklch(1 0 0); --surface-2: oklch(0.972 0.004 40);
  --ink: oklch(0.18 0.018 40); --muted: oklch(0.46 0.018 40); --line: oklch(0.9 0.008 40);
  --primary: oklch(0.5 0.151 40); --primary-strong: oklch(0.43 0.15 39); --on-primary: oklch(0.99 0 0);
  --success: oklch(0.48 0.12 152); --success-bg: oklch(0.96 0.03 152);
  --danger: oklch(0.55 0.16 28); --danger-bg: oklch(0.96 0.025 28);
  --focus: oklch(0.6 0.15 40 / 0.35);
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: oklch(0.17 0.01 40); --surface: oklch(0.21 0.012 40); --surface-2: oklch(0.25 0.012 40);
    --ink: oklch(0.94 0.008 40); --muted: oklch(0.7 0.015 40); --line: oklch(0.32 0.012 40);
    --primary: oklch(0.66 0.15 42); --primary-strong: oklch(0.72 0.14 45); --on-primary: oklch(0.16 0.02 40);
    --success: oklch(0.74 0.13 152); --success-bg: oklch(0.27 0.04 152);
    --danger: oklch(0.72 0.14 28); --danger-bg: oklch(0.28 0.05 28);
    --focus: oklch(0.7 0.14 42 / 0.4);
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 0.9375rem/1.55 Inter, "Segoe UI", system-ui, -apple-system, sans-serif;
}
.top { border-bottom: 1px solid var(--line); background: var(--surface); }
.top-inner, main { max-width: 42rem; margin: 0 auto; padding: 0 1rem; }
body.wide .top-inner, body.wide main { max-width: 76rem; }
.layout { display: grid; gap: 1rem; grid-template-columns: 15.5rem minmax(0, 1fr) 19rem; align-items: start; }
.layout > * { margin-bottom: 0; }
.rail, .aside { position: sticky; top: 1rem; }
.aside pre { font-size: 0.75rem; white-space: pre-wrap; overflow-wrap: anywhere; }
.aside .lead { font-size: 0.8125rem; }
.step { display: inline-grid; place-items: center; width: 1.375rem; height: 1.375rem; border-radius: 50%; background: var(--surface-2); border: 1px solid var(--line); color: var(--muted); font-size: 0.75rem; font-weight: 650; font-variant-numeric: tabular-nums; flex: none; }
.rail .empty { font-size: 0.8125rem; }
@media (max-width: 64rem) { .layout { grid-template-columns: minmax(0, 1fr) 18rem; } .rail { grid-column: 1 / -1; position: static; } }
@media (max-width: 46rem) { .layout { grid-template-columns: minmax(0, 1fr); } .aside { position: static; } }
.top-inner { display: flex; align-items: center; gap: 0.625rem; height: 3.5rem; }
.top img { width: 1.75rem; height: 1.75rem; border-radius: 0.375rem; }
.brand { font-weight: 650; letter-spacing: -0.01em; }
.top .meta { margin-left: auto; color: var(--muted); font-size: 0.8125rem; }
main { padding-top: 2rem; padding-bottom: 3rem; }
h1 { font-size: 1.5rem; line-height: 1.25; letter-spacing: -0.015em; margin: 0 0 0.375rem; }
.lead { color: var(--muted); margin: 0 0 1.5rem; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 0.5rem; margin-bottom: 1rem; }
.panel > header { padding: 0.875rem 1.125rem; border-bottom: 1px solid var(--line); display: flex; align-items: baseline; gap: 0.5rem; }
.panel h2 { font-size: 0.9375rem; margin: 0; }
.panel .count { color: var(--muted); font-size: 0.8125rem; font-variant-numeric: tabular-nums; }
.panel .body { padding: 1.125rem; }
.list { list-style: none; margin: 0; padding: 0; }
.list li { display: flex; align-items: center; gap: 0.75rem; padding: 0.625rem 1.125rem; border-top: 1px solid var(--line); }
.list li:first-child { border-top: 0; }
.dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--success); flex: none; }
.mono { font-family: ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace; font-size: 0.875rem; }
.list form { margin-left: auto; }
.empty { color: var(--muted); padding: 1rem 1.125rem; margin: 0; }
.grid { display: grid; grid-template-columns: 1fr 6.5rem; gap: 0.875rem 0.75rem; }
.full { grid-column: 1 / -1; }
label { display: block; font-size: 0.8125rem; font-weight: 550; margin-bottom: 0.3rem; }
.hint { display: block; color: var(--muted); font-size: 0.75rem; font-weight: 400; margin-top: 0.3rem; }
input, select {
  width: 100%; font: inherit; color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: 0.5rem; padding: 0.5rem 0.65rem; min-height: 2.5rem;
  transition: border-color 160ms, box-shadow 160ms;
}
input::placeholder { color: var(--muted); opacity: 0.7; }
input:focus, select:focus, button:focus-visible, summary:focus-visible { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px var(--focus); }
details.alt { margin-top: 1rem; border-top: 1px dashed var(--line); padding-top: 0.875rem; }
summary { cursor: pointer; color: var(--primary-strong); font-size: 0.8125rem; font-weight: 550; border-radius: 0.25rem; }
details.alt[open] summary { margin-bottom: 0.75rem; }
.actions { display: flex; align-items: center; gap: 0.75rem; margin-top: 1.25rem; }
button {
  font: inherit; font-weight: 600; cursor: pointer; border-radius: 0.5rem; min-height: 2.5rem; padding: 0 1rem;
  border: 1px solid transparent; transition: background-color 160ms, border-color 160ms;
}
.primary { background: var(--primary); color: var(--on-primary); }
.primary:hover { background: var(--primary-strong); }
.ghost { background: transparent; color: var(--muted); border-color: var(--line); min-height: 2rem; font-size: 0.8125rem; padding: 0 0.75rem; }
.ghost:hover { color: var(--danger); border-color: var(--danger); }
.actions .note { color: var(--muted); font-size: 0.8125rem; }
.banner { border-radius: 0.5rem; padding: 0.75rem 1rem; margin-bottom: 1rem; border: 1px solid; }
.banner.error { background: var(--danger-bg); border-color: color-mix(in oklch, var(--danger) 35%, transparent); }
.banner.ok { background: var(--success-bg); border-color: color-mix(in oklch, var(--success) 35%, transparent); }
.banner strong { display: block; margin-bottom: 0.125rem; }
.facts { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.5rem; font-size: 0.8125rem; color: var(--muted); }
.facts li { padding-left: 1.25rem; position: relative; }
.facts li::before { content: ""; position: absolute; left: 0.25rem; top: 0.55em; width: 0.4rem; height: 0.4rem; border-radius: 1px; background: var(--primary); }
pre { margin: 0.75rem 0 0; padding: 0.75rem; background: var(--surface-2); border: 1px solid var(--line); border-radius: 0.5rem; overflow-x: auto; font-size: 0.8125rem; line-height: 1.5; }
.link { color: var(--primary-strong); font-weight: 600; text-decoration: none; }
.link:hover { text-decoration: underline; }
footer { color: var(--muted); font-size: 0.75rem; text-align: center; margin-top: 2rem; }
@media (max-width: 30rem) { .grid { grid-template-columns: 1fr; } .top .meta { display: none; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""


def shell(title: str, body: str, meta: str = "", wide: bool = False) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        "<meta name=robots content=noindex>"
        f"<title>{escape(title)} · TableFox</title><link rel=icon href=/icon.png>"
        f"<style>{STYLE}</style></head><body{' class=wide' if wide else ''}>"
        "<div class=top><div class=top-inner><img src=/icon.png alt=''>"
        f"<span class=brand>TableFox</span><span class=meta>{escape(meta)}</span></div></div>"
        f"<main>{body}<footer>TableFox · read-only PostgreSQL for ChatGPT</footer></main></body></html>"
    )


def _banner(kind: str, heading: str, text: str) -> str:
    return f"<div class='banner {kind}' role=status><strong>{escape(heading)}</strong>{escape(text)}</div>"


def connect_page(token: str, names: list[str], values: dict[str, str] | None = None, error: str = "") -> str:
    values = values or {}
    token_field = f"<input type=hidden name=t value='{escape(token, quote=True)}'>"

    def value(key: str, default: str = "") -> str:
        return escape(values.get(key, default), quote=True)

    rows = "".join(
        f"<li><span class=dot aria-hidden=true></span><span class=mono>{escape(name)}</span>"
        f"<form method=post>{token_field}<input type=hidden name=action value=delete>"
        f"<input type=hidden name=name value='{escape(name, quote=True)}'>"
        f"<button class=ghost aria-label='Remove {escape(name, quote=True)}'>Remove</button></form></li>"
        for name in names
    )
    saved = f"<ul class=list>{rows}</ul>" if names else "<p class=empty>None yet. Add your first database to start asking questions.</p>"
    default_name = "" if names else "main"
    sslmode = values.get("sslmode", "require")
    ssl_options = "".join(
        f"<option value={mode}{' selected' if mode == sslmode else ''}>{label}</option>"
        for mode, label in (("require", "Require (encrypted)"), ("verify-full", "Verify full (encrypted + certificate check)"))
    )
    body = (
        "<h1>Connect a database</h1>"
        "<p class=lead>Add a read-only PostgreSQL connection. TableFox tests it before saving, "
        "and ChatGPT never sees these details.</p>"
        + (_banner("error", "Not connected", error) if error else "")
        + "<div class=layout>"
        "<section class='panel rail' aria-labelledby=saved-h><header><span class=step>1</span><h2 id=saved-h>Connected</h2>"
        f"<span class=count>{len(names)}</span></header>{saved}</section>"
        "<section class=panel aria-labelledby=add-h><header><span class=step>2</span><h2 id=add-h>Add a database</h2></header><div class=body>"
        f"<form method=post>{token_field}<div class=grid>"
        "<div class=full><label for=name>Name</label>"
        f"<input id=name name=name required maxlength=32 pattern='[a-zA-Z0-9][a-zA-Z0-9_\\-]{{0,31}}' "
        f"value='{value('name', default_name)}' placeholder='sales' autocomplete=off>"
        "<span class=hint>How you refer to it in ChatGPT, e.g. &ldquo;use sales&rdquo;.</span></div>"
        "<div><label for=host>Host</label>"
        f"<input id=host name=host value='{value('host')}' placeholder='db.example.com' autocomplete=off spellcheck=false></div>"
        "<div><label for=port>Port</label>"
        f"<input id=port name=port value='{value('port', '5432')}' inputmode=numeric pattern='[0-9]{{1,5}}'></div>"
        "<div class=full><label for=dbname>Database</label>"
        f"<input id=dbname name=dbname value='{value('dbname')}' placeholder='postgres' autocomplete=off spellcheck=false></div>"
        "<div class=full><label for=user>User</label>"
        f"<input id=user name=user value='{value('user')}' placeholder='tablefox_reader' autocomplete=off spellcheck=false></div>"
        "<div class=full><label for=password>Password</label>"
        "<input id=password name=password type=password autocomplete=new-password></div>"
        f"<div class=full><label for=sslmode>SSL</label><select id=sslmode name=sslmode>{ssl_options}</select></div>"
        "</div>"
        "<details class=alt><summary>Paste a connection URL instead</summary>"
        "<label for=database_url>Connection URL</label>"
        "<input id=database_url name=database_url type=password autocomplete=off "
        "placeholder='postgresql://user:password@host:5432/database?sslmode=require'>"
        "<span class=hint>Used instead of the fields above when filled in.</span></details>"
        "<div class=actions><button class='primary'>Test and connect</button>"
        "<span class=note>Takes a few seconds.</span></div></form></div></section>"
        "<aside class='panel aside' aria-labelledby=safe-h><header><span class=step>3</span><h2 id=safe-h>Before you connect</h2></header><div class=body>"
        "<ul class=facts>"
        "<li>Only read-only users are accepted. Users that can write or change the schema are refused.</li>"
        "<li>The connection must use SSL and a public address. Allow TableFox's host in your database firewall.</li>"
        "<li>Details are stored encrypted. You can remove a database here at any time.</li>"
        "</ul>"
        "<details class=alt open><summary>How do I create a read-only user?</summary>"
        "<p class=lead style='margin:0'>Run this as an administrator, replacing the &lt;placeholders&gt;:</p>"
        f"<pre class=mono>{escape(READER_SQL)}</pre></details></div></aside></div>"
    )
    return shell("Connect a database", body, "Private link · single use", wide=True)


def done_page(message: str, next_token: str) -> str:
    more = escape(next_token, quote=True)
    body = (
        "<h1>All set</h1>"
        + _banner("ok", "Saved", message)
        + "<section class=panel><div class=body><p style='margin:0 0 0.75rem'>"
        "Return to ChatGPT and ask your question. If you have several databases, name the one you mean, "
        "for example &ldquo;in sales, how many orders shipped last week?&rdquo;</p>"
        f"<a class=link href='/connect/{more}'>Add or remove another database &rarr;</a></div></section>"
    )
    return shell("Saved", body)


def message_page(title: str, text: str) -> str:
    body = f"<h1>{escape(title)}</h1><p class=lead>{escape(text)}</p>"
    return shell(title, body)


POLICY_DATE = "10 October 2026"


def _policy(title: str, sections: list[tuple[str, str]]) -> str:
    parts = "".join(
        f"<section class=panel><header><h2>{escape(heading)}</h2></header><div class=body>{text}</div></section>"
        for heading, text in sections
    )
    body = (
        f"<h1>{escape(title)}</h1><p class=lead>Last updated {POLICY_DATE}. "
        "<a class=link href='/'>Back to TableFox</a></p>" + parts
    )
    return shell(title, body)


def privacy_page() -> str:
    return _policy(
        "Privacy",
        [
            ("What TableFox stores", ("<ul class=facts>"
             "<li>Your sign-in identifier from our login provider, to link your saved databases to you. Logs and cache folders use only a one-way hash of it.</li>"
             "<li>The database connections you save (host, port, database, user, password), encrypted with a server key.</li>"
             "<li>A cache of your database's schema: table, column and constraint names and comments. Never table rows.</li>"
             "<li>An audit log of tool calls: time, action, object names, counts and a SHA-256 fingerprint of each SQL "
             "statement. Never the SQL text or any query results.</li>"
             "<li>Single-use connect-link records, kept until they expire.</li></ul>")),
            ("What TableFox does not store", ("<p>Query results and table rows are returned to your ChatGPT conversation "
             "and are not saved, indexed or cached by TableFox.</p>")),
            ("Who processes data", ("<ul class=facts>"
             "<li>OpenAI (ChatGPT) receives the results of the questions you ask, under OpenAI's own privacy policy.</li>"
             "<li>Auth0 handles sign-in. Render hosts the TableFox server. Neon stores the encrypted connection records.</li>"
             "<li>Your database provider is contacted only with the read-only user you supply.</li></ul>")),
            ("Your choices", ("<p>Remove any saved database at any time on your private manage page (ask ChatGPT for "
             "&ldquo;the TableFox link to manage my databases&rdquo;). Disconnecting TableFox in ChatGPT stops all access. "
             "To delete everything associated with your account, open an issue on "
             "<a class=link href='https://github.com/Adhrit-Verma/TableFox/issues'>GitHub</a>.</p>")),
            ("Security", ("<p>Connections require SSL and a public address; only read-only database users are accepted; "
             "every query runs inside a read-only transaction with timeouts; personal-data columns such as emails and "
             "passwords are blocked from results.</p>")),
        ],
    )


def terms_page() -> str:
    return _policy(
        "Terms",
        [
            ("Using TableFox", ("<p>TableFox lets ChatGPT read a PostgreSQL database you connect. Only connect databases "
             "you are authorised to access, using a read-only user. You are responsible for the data you choose to expose "
             "and for complying with the rules that apply to it.</p>")),
            ("No warranty", ("<p>TableFox is provided free of charge and &ldquo;as is&rdquo;, without warranties of any kind. "
             "Answers are generated by ChatGPT from query results and can be wrong; verify anything important.</p>")),
            ("Limits and changes", ("<p>The service may change, be rate-limited or stop at any time. Access may be removed "
             "for misuse, including attempts to reach systems you do not own or to bypass TableFox's safety controls.</p>")),
            ("Liability", ("<p>To the extent permitted by law, the authors are not liable for any loss arising from use "
             "of TableFox.</p>")),
            ("Contact", ("<p>Questions and requests: "
             "<a class=link href='https://github.com/Adhrit-Verma/TableFox/issues'>GitHub issues</a>.</p>")),
        ],
    )
