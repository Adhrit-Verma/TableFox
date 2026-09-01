import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.audit import AuditLog
from dbmap.context import apply_context
from dbmap.diff import compare_snapshots, schema_fingerprint
from dbmap.models import GraphEdge, GraphNode, GraphSnapshot
from dbmap.security import ApiKeyAuth, filter_metadata_schemas


class GovernanceTests(unittest.TestCase):
    def test_context_requires_database_match_and_gates_code_links_by_fingerprint(self):
        snapshot = GraphSnapshot.create(
            "db.example/app",
            [GraphNode("table:public.customers", "table", "public.customers", "public")],
            [],
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "context.json"
            path.write_text(
                json.dumps(
                    {
                        "database": snapshot.database,
                        "schema_fingerprint": schema_fingerprint(snapshot),
                        "objects": {
                            "table:public.customers": {
                                "owner": "billing",
                                "source_of_truth": True,
                                "documents": [
                                    {
                                        "title": "Data contract",
                                        "source": "internal-wiki",
                                        "updated_at": "2026-07-23",
                                    }
                                ],
                                "code_links": [
                                    {
                                        "kind": "orm",
                                        "path": "app/models/customer.py",
                                        "revision": "abc123",
                                    }
                                ],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            enriched = apply_context(snapshot, path)

        context = enriched.nodes[0].metadata["context"]
        self.assertTrue(context["source_of_truth"])
        self.assertEqual(context["code_links_status"], "matched")

    def test_snapshot_diff_reports_confirmed_changes(self):
        before = GraphSnapshot.create("db", [], [])
        after = GraphSnapshot.create(
            "db",
            [GraphNode("table:public.orders", "table", "public.orders", "public")],
            [],
        )

        result = compare_snapshots(before, after)

        self.assertTrue(result["changed"])
        self.assertEqual(result["nodes"]["added"], ["table:public.orders"])

    def test_snapshot_diff_keeps_dependencies_for_removed_objects(self):
        before = GraphSnapshot.create(
            "db",
            [
                GraphNode("table:public.customers", "table", "public.customers", "public"),
                GraphNode("table:public.orders", "table", "public.orders", "public"),
            ],
            [
                GraphEdge(
                    "foreign_key:orders:customers",
                    "foreign_key",
                    "table:public.orders",
                    "table:public.customers",
                )
            ],
        )
        after = GraphSnapshot.create(
            "db",
            [GraphNode("table:public.customers", "table", "public.customers", "public")],
            [],
        )

        result = compare_snapshots(before, after)

        self.assertEqual(len(result["impact"]["confirmed_dependencies"]), 1)

    def test_hashed_api_key_roles_and_schema_policy(self):
        token = "test-secret-key"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text(
                json.dumps(
                    {
                        "users": [
                            {
                                "name": "alice",
                                "role": "viewer",
                                "key_sha256": hashlib.sha256(token.encode()).hexdigest(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            principal = ApiKeyAuth(path, required=True).authenticate(f"Bearer {token}")

        self.assertTrue(principal.can("metadata"))
        self.assertFalse(principal.can("query"))
        self.assertEqual(
            ApiKeyAuth(None, required=False).authenticate("Bearer stale-key").name,
            "local-user",
        )
        metadata = {
            "relations": [
                {"schema": "public", "name": "orders"},
                {"schema": "private", "name": "secrets"},
            ],
            "constraints": [
                {
                    "schema": "public",
                    "table": "orders",
                    "foreign_schema": None,
                }
            ],
        }
        filtered = filter_metadata_schemas(metadata, ("public",), ("private",))
        self.assertEqual([row["name"] for row in filtered["relations"]], ["orders"])
        self.assertEqual(len(filtered["constraints"]), 1)

    def test_missing_context_file_is_tolerated_but_a_wrong_one_is_not(self):
        snapshot = GraphSnapshot.create(
            "db.example/app",
            [GraphNode("table:public.customers", "table", "public.customers", "public")],
            [],
        )
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "not-created-yet.json"

            unchanged = apply_context(snapshot, missing)

            wrong_database = Path(directory) / "wrong.json"
            wrong_database.write_text(
                json.dumps({"database": "someone-elses-db", "objects": {}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                apply_context(snapshot, wrong_database)

        self.assertEqual(unchanged.nodes[0].metadata.get("context"), None)

    def test_restricted_schema_is_enforced_before_metadata_is_cached(self):
        from dbmap.security import schema_allowed

        metadata = {
            "relations": [
                {"schema": "public", "name": "orders"},
                {"schema": "vault", "name": "secrets"},
            ],
            "columns": [
                {"schema": "vault", "table": "secrets", "name": "token"},
                {"schema": "public", "table": "orders", "name": "id"},
            ],
        }

        filtered = filter_metadata_schemas(metadata, (), ("vault",))

        self.assertEqual([row["name"] for row in filtered["relations"]], ["orders"])
        self.assertEqual([row["name"] for row in filtered["columns"]], ["id"])
        # A restricted schema stays blocked even when it is also explicitly allowed.
        self.assertFalse(schema_allowed("vault", ("vault",), ("vault",)))

    def test_unsafe_sql_is_rejected_for_every_statement_shape(self):
        from dbmap.readonly import validate_readonly_sql

        unsafe = [
            "insert into public.orders values (1)",
            "update public.orders set id = 2",
            "delete from public.orders",
            "drop table public.orders",
            "alter table public.orders add column x int",
            "truncate public.orders",
            "grant select on public.orders to someone",
            "select 1; select 2",
            "select * into copied from public.orders",
            "select pg_read_file('/etc/passwd')",
            "select id from public.orders for update",
            "select pg_terminate_backend(1)",
        ]
        for statement in unsafe:
            with self.subTest(statement=statement):
                with self.assertRaises(ValueError):
                    validate_readonly_sql(statement)

        self.assertEqual(
            validate_readonly_sql("with recent as (select 1) select * from recent"),
            "with recent as (select 1) select * from recent",
        )

    def test_audit_log_records_hashes_without_sql_text(self):
        with TemporaryDirectory() as directory:
            audit = AuditLog(Path(directory))
            audit.record(
                "alice",
                "readonly_query",
                details={"sql_sha256": hashlib.sha256(b"select 1").hexdigest()},
            )
            content = next(Path(directory).glob("audit-*.jsonl")).read_text(encoding="utf-8")

        self.assertIn("sql_sha256", content)
        self.assertNotIn("select 1", content)


if __name__ == "__main__":
    unittest.main()
