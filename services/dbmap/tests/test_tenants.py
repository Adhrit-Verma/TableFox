import asyncio
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.fernet import Fernet
from dbmap.config import Settings
from dbmap.mcp_server import create_mcp
from dbmap.tenants import CredentialStore, TenantError, TenantRegistry, pin_database_url


def resolver(address):
    def resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolve


def base_settings(root: Path) -> Settings:
    return Settings(
        database_url=None,
        host="localhost",
        port=5432,
        database="postgres",
        user="postgres",
        password="",
        sslmode="prefer",
        cache_dir=root / "cache",
        statement_timeout_ms=5000,
        max_query_rows=200,
        api_host="127.0.0.1",
        api_port=8000,
        audit_dir=root / "audit",
        context_file=root / "operator-context.json",
        baseline_file=root / "operator-baseline.json",
    )


class PinDatabaseUrlTests(unittest.TestCase):
    def test_pins_public_address_and_forces_ssl(self):
        pinned = pin_database_url(
            "postgresql://reader:pw@db.example.com/app", {5432}, resolve=resolver("8.8.4.4")
        )
        self.assertIn("hostaddr=8.8.4.4", pinned)
        self.assertIn("sslmode=require", pinned)
        self.assertTrue(pinned.startswith("postgresql://reader:pw@db.example.com/app?"))

    def test_keeps_stronger_sslmode(self):
        pinned = pin_database_url(
            "postgresql://r:p@db.example.com/app?sslmode=verify-full", {5432}, resolve=resolver("8.8.8.8")
        )
        self.assertIn("sslmode=verify-full", pinned)

    def test_rejects_internal_addresses(self):
        for address in ("127.0.0.1", "10.0.0.5", "192.168.1.2", "169.254.169.254", "100.64.0.1", "203.0.113.10", "::1", "::ffff:127.0.0.1", "fd00::1"):
            with self.subTest(address=address), self.assertRaises(TenantError):
                pin_database_url("postgresql://r:p@db.example.com/app", {5432}, resolve=resolver(address))

    def test_rejects_unsafe_url_shapes(self):
        public = resolver("8.8.8.8")
        for url in (
            "mysql://r:p@db.example.com/app",
            "postgresql://r:p@a.example.com,b.example.com/app",
            "postgresql://r:p@db.example.com:6543/app",
            "postgresql://r:p@db.example.com:notaport/app",
            "postgresql://r:p@db.example.com/app?host=127.0.0.1",
            "postgresql://r:p@db.example.com/app?hostaddr=127.0.0.1",
            "postgresql://r:p@db.example.com/app?options=-c%20default_transaction_read_only%3Doff",
            "postgresql://r:p@db.example.com/app?service=internal",
            "postgresql://db.example.com/app",
            "postgresql://r:p@db.example.com/",
        ):
            with self.subTest(url=url), self.assertRaises(TenantError):
                pin_database_url(url, {5432}, resolve=public)


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tenants.sqlite"
        self.store = CredentialStore(self.path, Fernet.generate_key().decode())

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_round_trip_is_encrypted_at_rest(self):
        self.store.put("auth0|alice", "main", "postgresql://reader:s3cret-pw@db.example.com/app")
        self.assertEqual(self.store.get("auth0|alice", "main"), "postgresql://reader:s3cret-pw@db.example.com/app")
        self.assertNotIn(b"s3cret-pw", self.path.read_bytes())
        self.assertIsNone(self.store.get("auth0|bob", "main"))
        self.store.delete("auth0|alice", "main")
        self.assertIsNone(self.store.get("auth0|alice", "main"))

    def test_link_tokens_are_signed_expiring_and_single_use(self):
        token = self.store.make_link_token("auth0|alice")
        payload = self.store.read_link_token(token)
        self.assertEqual(payload["sub"], "auth0|alice")

        body, _, signature = token.partition(".")
        with self.assertRaises(TenantError):
            self.store.read_link_token(f"{body}.{'0' * len(signature)}")
        with self.assertRaises(TenantError):
            self.store.read_link_token(token, now=payload["exp"] + 1)

        self.store.consume_link(payload)
        with self.assertRaises(TenantError):
            self.store.read_link_token(token)

    def test_other_key_cannot_read_links(self):
        other = CredentialStore(Path(self.tmp.name) / "other.sqlite", Fernet.generate_key().decode())
        try:
            with self.assertRaises(TenantError):
                other.read_link_token(self.store.make_link_token("auth0|alice"))
        finally:
            other.close()


class FakeService:
    def __init__(self, settings):
        self.settings = settings
        self.closed = False
        self.introspector = SimpleNamespace(close=self.close)

    def close(self):
        self.closed = True

    def connectivity_check(self, actor):
        return {"database_url": self.settings.database_url, "actor": actor}


class TenantRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = CredentialStore(root / "tenants.sqlite", Fernet.generate_key().decode())
        self.registry = TenantRegistry(base_settings(root), self.store, {5432}, max_active=1, factory=FakeService)
        self.public = resolver("8.8.8.8")
        from dbmap import tenants

        self._real_resolve = tenants.socket.getaddrinfo
        tenants.socket.getaddrinfo = self.public

    def tearDown(self):
        from dbmap import tenants

        tenants.socket.getaddrinfo = self._real_resolve
        self.store.close()
        self.tmp.cleanup()

    def test_users_are_isolated(self):
        self.store.put("alice", "main", "postgresql://a:pw@a.example.com/one")
        self.store.put("bob", "main", "postgresql://b:pw@b.example.com/two")
        alice = self.registry.service_for("alice", "main").settings
        bob = self.registry.service_for("bob", "main").settings

        self.assertIn("a.example.com", alice.database_url)
        self.assertIn("b.example.com", bob.database_url)
        self.assertNotEqual(alice.cache_dir, bob.cache_dir)
        self.assertNotEqual(alice.audit_dir, bob.audit_dir)
        self.assertNotEqual(alice.runtime_file, bob.runtime_file)
        self.assertNotEqual(alice.mcp_actor, bob.mcp_actor)
        self.assertNotIn("alice", alice.mcp_actor)
        self.assertIsNone(alice.context_file)
        self.assertIsNone(alice.baseline_file)

    def test_unknown_user_has_no_service(self):
        self.assertIsNone(self.registry.service_for("nobody", "main"))

    def test_eviction_and_forget_close_pools(self):
        self.store.put("alice", "main", "postgresql://a:pw@a.example.com/one")
        self.store.put("bob", "main", "postgresql://b:pw@b.example.com/two")
        alice = self.registry.service_for("alice", "main")
        self.registry.service_for("bob", "main")
        self.assertTrue(alice.closed)

        self.registry.forget("bob", "main")
        self.assertIsNone(self.store.get("bob", "main"))

    def test_connect_rejects_writer_roles_without_saving(self):
        def refuse(_url):
            raise TenantError("TableFox only accepts read-only roles")

        with self.assertRaises(TenantError):
            self.registry.connect("alice", "main", "postgresql://a:pw@a.example.com/one", check=refuse)
        self.assertIsNone(self.store.get("alice", "main"))

        self.registry.connect("alice", "main", "postgresql://a:pw@a.example.com/one", check=lambda _url: None)
        self.assertIsNotNone(self.store.get("alice", "main"))


class MultiTenantMcpTests(TenantRegistryTests):
    def call_as(self, mcp, user_id, name, arguments=None):
        from mcp.server.auth.middleware.auth_context import auth_context_var
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
        from mcp.server.auth.provider import AccessToken

        token = auth_context_var.set(
            AuthenticatedUser(AccessToken(token="t", client_id="chatgpt", scopes=[], subject=user_id))
        )
        try:
            return asyncio.run(mcp.call_tool(name, arguments or {}))
        finally:
            auth_context_var.reset(token)

    def test_tools_use_the_callers_database(self):
        self.store.put("alice", "main", "postgresql://a:pw@a.example.com/one")
        mcp = create_mcp(registry=self.registry, public_url="https://tablefox.example.com")

        result = self.call_as(mcp, "alice", "database_connectivity_check")
        self.assertFalse(result.is_error)
        self.assertIn("a.example.com", str(result.structured_content))

    def test_missing_connection_returns_private_link(self):
        from mcp.server.mcpserver.exceptions import ToolError

        mcp = create_mcp(registry=self.registry, public_url="https://tablefox.example.com")
        with self.assertRaisesRegex(ToolError, r"https://tablefox\.example\.com/connect\?t="):
            self.call_as(mcp, "carol", "database_connectivity_check")

    def test_several_databases_ask_which_one(self):
        from mcp.server.mcpserver.exceptions import ToolError

        self.store.put("alice", "hr", "postgresql://a:pw@hr.example.com/hr")
        self.store.put("alice", "sales", "postgresql://a:pw@sales.example.com/sales")
        mcp = create_mcp(registry=self.registry, public_url="https://tablefox.example.com")

        with self.assertRaisesRegex(ToolError, "Several databases are connected: hr, sales"):
            self.call_as(mcp, "alice", "database_connectivity_check")
        result = self.call_as(mcp, "alice", "database_connectivity_check", {"database": "sales"})
        self.assertIn("sales.example.com", str(result.structured_content))
        self.assertIn("user:", str(result.structured_content))
        with self.assertRaisesRegex(ToolError, "No database named 'other'"):
            self.call_as(mcp, "alice", "database_connectivity_check", {"database": "other"})
        # another user's name is never reachable
        with self.assertRaisesRegex(ToolError, "No database is connected"):
            self.call_as(mcp, "bob", "database_connectivity_check", {"database": "sales"})

    def test_connection_names_are_validated(self):
        for bad in ("", "Has Space", "a" * 33, "../x", "x;drop"):
            with self.subTest(name=bad), self.assertRaises(TenantError):
                self.registry.connect("alice", bad, "postgresql://a:pw@a.example.com/one", check=lambda _url: None)

    def test_multi_tenant_tools_keep_explicit_annotations(self):
        mcp = create_mcp(registry=self.registry, public_url="https://tablefox.example.com")
        tools = asyncio.run(mcp.list_tools())
        self.assertIn("database_connections", {tool.name for tool in tools})
        for tool in tools:
            self.assertIsInstance(tool.annotations.read_only_hint, bool, tool.name)
            self.assertFalse(tool.annotations.destructive_hint, tool.name)


if __name__ == "__main__":
    unittest.main()
