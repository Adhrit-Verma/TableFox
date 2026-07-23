import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.config import Settings
from dbmap.diff import schema_fingerprint
from dbmap.models import GraphNode, GraphSnapshot
from dbmap.service import DatabaseMapService


class FakeIntrospector:
    def __init__(self) -> None:
        self.snapshot_value = GraphSnapshot.create(
            "testdb",
            [
                GraphNode("schema:public", "schema", "public", "public", "public"),
                GraphNode(
                    "table:public.customers",
                    "table",
                    "public.customers",
                    "public",
                    "customers",
                    "schema:public",
                ),
            ],
            [],
        )
        self.query_limit = None
        self.include_plan = None

    def connectivity_check(self):
        return {"ok": True}

    def snapshot(self, refresh: bool = False):
        return self.snapshot_value

    def readonly_query(self, sql: str, limit: int, approved: bool = False):
        self.query_limit = limit
        return {"sql": sql, "limit": limit, "approved": approved}

    def explain_query(self, sql: str, include_plan: bool = False):
        self.include_plan = include_plan
        return {
            "sql": sql,
            "executed": False,
            "within_policy": True,
            "summary": {"relations": []},
        }


class DatabaseMapServiceTests(unittest.TestCase):
    def test_shared_use_cases_apply_bounds_and_delegate(self):
        introspector = FakeIntrospector()
        service = DatabaseMapService(introspector)

        self.assertEqual(len(service.graph_snapshot(max_nodes=-1).nodes), 1)
        self.assertEqual(service.search("customers", limit=500)[0]["id"], "table:public.customers")
        self.assertTrue(service.explain_object("table:public.customers")["found"])
        self.assertEqual(service.readonly_query("select 1", limit=-1)["limit"], 1)
        self.assertEqual(introspector.query_limit, 1)
        self.assertFalse(service.explain_query("select 1")["executed"])
        self.assertFalse(introspector.include_plan)

    def test_context_resolves_source_of_truth_and_baseline_comparison(self):
        introspector = FakeIntrospector()
        snapshot = introspector.snapshot_value
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context_path = root / "context.json"
            baseline_path = root / "baseline.json"
            context_path.write_text(
                json.dumps(
                    {
                        "database": snapshot.database,
                        "schema_fingerprint": schema_fingerprint(snapshot),
                        "objects": {
                            "table:public.customers": {
                                "owner": "crm-team",
                                "source_of_truth": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            baseline_path.write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")
            settings = Settings(
                database_url=None,
                host="localhost",
                port=5432,
                database="testdb",
                user="reader",
                password="",
                sslmode="prefer",
                cache_dir=root,
                statement_timeout_ms=5000,
                max_query_rows=200,
                api_host="127.0.0.1",
                api_port=8000,
                context_file=context_path,
                baseline_file=baseline_path,
            )
            service = DatabaseMapService(introspector, settings=settings)

            source = service.source_of_truth("customers")
            changes = service.schema_changes()

        self.assertTrue(source["resolved"])
        self.assertEqual(source["candidates"][0]["status"], "verified")
        self.assertFalse(changes["changed"])


if __name__ == "__main__":
    unittest.main()
