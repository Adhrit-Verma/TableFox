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
