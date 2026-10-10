"""Multi-user mode: one isolated DatabaseMapService per signed-in user.

Credentials are entered on the /connect page (never in chat), checked, and stored
encrypted. Every connection is pinned to an address resolved and vetted here, so a
user cannot point the hosted server at loopback, private, or metadata addresses.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import sqlite3
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Settings
from .service import DatabaseMapService, build_service

NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
ALLOWED_QUERY_KEYS = {"sslmode", "channel_binding", "connect_timeout", "application_name"}
STRONG_SSLMODES = {"require", "verify-ca", "verify-full"}
LINK_TTL_SECONDS = 600

WRITE_PRIVILEGE_SQL = """
select
  (select rolsuper or rolcreaterole or rolcreatedb or rolreplication or rolbypassrls
     from pg_roles where rolname = current_user) as privileged_role,
  has_database_privilege(current_database(), 'CREATE') as database_create,
  exists (
    select 1 from pg_namespace n
    where n.nspname not in ('pg_catalog', 'information_schema') and n.nspname not like 'pg_%'
      and has_schema_privilege(n.oid, 'CREATE')
  ) as schema_create,
  exists (
    select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('r', 'p', 'v', 'm', 'f')
      and n.nspname not in ('pg_catalog', 'information_schema') and n.nspname not like 'pg_%'
      and has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')
  ) as table_write
"""


class TenantError(ValueError):
    """A user-facing problem with a tenant's connection; never contains credentials."""


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def pin_database_url(url: str, allowed_ports: set[int], resolve=None) -> str:
    """Validate a user's PostgreSQL URL and return it pinned to one vetted public address."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"postgres", "postgresql"}:
        raise TenantError("Use a postgresql:// URL.")
    if "," in parts.netloc or not parts.hostname:
        raise TenantError("Give exactly one database host.")
    if not parts.username or not parts.path.strip("/"):
        raise TenantError("The URL must include a user and a database name.")
    try:
        port = parts.port or 5432
    except ValueError as error:
        raise TenantError("The port is not a number.") from error
    if port not in allowed_ports:
        raise TenantError(f"Port {port} is not allowed.")
    query = dict(parse_qsl(parts.query))
    unknown = set(query) - ALLOWED_QUERY_KEYS
    if unknown:
        raise TenantError(f"Unsupported URL options: {', '.join(sorted(unknown))}.")
    if query.get("sslmode") not in STRONG_SSLMODES:
        query["sslmode"] = "require"

    try:
        infos = (resolve or socket.getaddrinfo)(parts.hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise TenantError("The database host could not be resolved.") from error
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_public(address) for address in addresses):
        raise TenantError("The database host must be a public internet address.")
    # Pin the vetted address so a later DNS change cannot redirect the connection;
    # host stays in the URL for TLS name checks.
    query["hostaddr"] = min(addresses)
    query.setdefault("connect_timeout", "10")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class CredentialStore:
    """Encrypted per-user database URLs, in a SQLite file or an operator PostgreSQL database.

    Hosts without a persistent disk (e.g. Render's free tier) pass a postgresql:// URL.
    """

    def __init__(self, location: Path | str, key: str) -> None:
        from cryptography.fernet import Fernet

        self._fernet = Fernet(key.encode())
        self._link_key = hashlib.sha256(b"dbmap-connect-link" + key.encode()).digest()
        self._lock = Lock()
        self._postgres_url = str(location) if str(location).startswith(("postgres://", "postgresql://")) else None
        self._db = None
        if self._postgres_url is None:
            path = Path(location)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False)
        blob = "bytea" if self._postgres_url else "blob"
        self._run(
            f"create table if not exists connections (user_id text, name text, secret {blob} not null, "
            "primary key (user_id, name))"
        )
        self._run("create table if not exists used_links (nonce text primary key, expires bigint)")

    def _run(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Execute one statement in its own transaction and return its rows."""
        if self._postgres_url:
            import psycopg

            # A short connection per call survives serverless databases closing idle sessions.
            with psycopg.connect(self._postgres_url, autocommit=True, connect_timeout=10) as conn:
                cursor = conn.execute(sql.replace("?", "%s"), params)
                return cursor.fetchall() if cursor.description else []
        with self._lock, self._db:
            return self._db.execute(sql, params).fetchall()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()

    def names(self, user_id: str) -> list[str]:
        return [row[0] for row in self._run("select name from connections where user_id = ? order by name", (user_id,))]

    def get(self, user_id: str, name: str) -> str | None:
        rows = self._run("select secret from connections where user_id = ? and name = ?", (user_id, name))
        return self._fernet.decrypt(bytes(rows[0][0])).decode() if rows else None

    def put(self, user_id: str, name: str, database_url: str) -> None:
        self._run(
            "insert into connections values (?, ?, ?) "
            "on conflict (user_id, name) do update set secret = excluded.secret",
            (user_id, name, self._fernet.encrypt(database_url.encode())),
        )

    def delete(self, user_id: str, name: str) -> None:
        self._run("delete from connections where user_id = ? and name = ?", (user_id, name))

    def make_link_token(self, user_id: str, now: float | None = None) -> str:
        payload = {
            "sub": user_id,
            "exp": int((now or time.time()) + LINK_TTL_SECONDS),
            "nonce": secrets.token_urlsafe(12),
        }
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        signature = hmac.new(self._link_key, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def read_link_token(self, token: str, now: float | None = None) -> dict:
        """Return the link payload if the signature and expiry are valid."""
        body, _, signature = token.partition(".")
        expected = hmac.new(self._link_key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise TenantError("This connect link is not valid.")
        payload = json.loads(base64.urlsafe_b64decode(body))
        if payload["exp"] < (now or time.time()):
            raise TenantError("This connect link has expired. Ask ChatGPT for a new one.")
        if self._run("select 1 from used_links where nonce = ?", (payload["nonce"],)):
            raise TenantError("This connect link was already used. Ask ChatGPT for a new one.")
        return payload

    def consume_link(self, payload: dict) -> None:
        self._run("delete from used_links where expires < ?", (int(time.time()),))
        self._run("insert into used_links values (?, ?) on conflict do nothing", (payload["nonce"], payload["exp"]))


def check_reader_role(pinned_url: str) -> None:
    """Connect once and refuse any role that can change data or schema."""
    import psycopg

    try:
        with psycopg.connect(pinned_url, autocommit=True) as conn:
            row = conn.execute(WRITE_PRIVILEGE_SQL).fetchone()
    except psycopg.Error as error:
        # Driver messages can echo connection details, so only known causes are named.
        text = str(error).lower()
        reasons = {
            "does not support ssl": "The server does not accept SSL connections; TableFox requires SSL.",
            "password authentication failed": "Wrong user or password.",
            "does not exist": "That database or user does not exist.",
            "timeout expired": "The server did not answer. Check the host, port, and firewall.",
            "connection refused": "The server refused the connection. Check the host, port, and firewall.",
            "no pg_hba.conf entry": "The server does not allow connections from TableFox's address.",
        }
        reason = next((message for marker, message in reasons.items() if marker in text), None)
        raise TenantError(reason or "Could not connect. Check host, user, password, and SSL.") from None
    privileged, database_create, schema_create, table_write = row
    problems = [
        label
        for label, flag in (
            ("the role has superuser/create-role/replication rights", privileged),
            ("the role can create schemas in the database", database_create),
            ("the role can create objects in a schema", schema_create),
            ("the role can insert, update, delete, or truncate a table", table_write),
        )
        if flag
    ]
    if problems:
        raise TenantError(
            "TableFox only accepts read-only roles, but " + "; ".join(problems)
            + ". Create a role with only CONNECT, USAGE and SELECT"
            + " (on PostgreSQL 14 or older also run: revoke create on schema public from public)."
        )


def _close(service: DatabaseMapService) -> None:
    introspector = service.introspector
    introspector.close()
    # PostgresIntrospector registers close() at exit; drop it so closed pools are freed.
    atexit.unregister(introspector.close)


def clean_name(name: str) -> str:
    cleaned = name.strip().lower()
    if not NAME_PATTERN.fullmatch(cleaned):
        raise TenantError("Use a short name: lowercase letters, digits, - or _ (up to 32 characters).")
    return cleaned


class TenantRegistry:
    """Builds and caches one isolated service per saved database, keyed by token subject and name."""

    def __init__(
        self,
        base: Settings,
        store: CredentialStore,
        allowed_ports: set[int],
        max_active: int = 50,
        factory=build_service,
    ) -> None:
        self.base = base
        self.store = store
        self.allowed_ports = allowed_ports
        self.max_active = max_active
        self._factory = factory
        self._active: OrderedDict[tuple[str, str], DatabaseMapService] = OrderedDict()
        self._lock = Lock()

    @staticmethod
    def tenant_key(user_id: str) -> str:
        return hashlib.sha256(user_id.encode()).hexdigest()[:24]

    def settings_for(self, user_id: str, name: str, database_url: str) -> Settings:
        key = self.tenant_key(user_id)
        root = self.base.cache_dir / "tenants" / key / name
        return replace(
            self.base,
            database_url=database_url,
            cache_dir=root / "cache",
            audit_dir=self.base.audit_dir / key,
            runtime_file=root / "runtime.json",
            mcp_actor=f"user:{key}:{name}",
            context_file=None,
            baseline_file=None,
            auth_required=False,
            pool_min_size=1,
            pool_max_size=min(self.base.pool_max_size, 2),
        )

    def service_for(self, user_id: str, name: str) -> DatabaseMapService | None:
        slot = (user_id, name)
        with self._lock:
            service = self._active.get(slot)
            if service is not None:
                self._active.move_to_end(slot)
                return service
        stored = self.store.get(user_id, name)
        if stored is None:
            return None
        # Re-resolve on every build so a stored host that moved to a private address is refused.
        pinned = pin_database_url(stored, self.allowed_ports)
        service = self._factory(self.settings_for(user_id, name, pinned))
        with self._lock:
            self._active[slot] = service
            while len(self._active) > self.max_active:
                _, evicted = self._active.popitem(last=False)
                _close(evicted)
        return service

    def connect(self, user_id: str, name: str, database_url: str, check=check_reader_role) -> str:
        name = clean_name(name)
        pinned = pin_database_url(database_url, self.allowed_ports)
        check(pinned)
        self.store.put(user_id, name, database_url.strip())
        self.forget(user_id, name, delete=False)
        return name

    def forget(self, user_id: str, name: str, delete: bool = True) -> None:
        with self._lock:
            service = self._active.pop((user_id, name), None)
        if service is not None:
            _close(service)
        if delete:
            self.store.delete(user_id, name)
