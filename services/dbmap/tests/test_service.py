from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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

    def readonly_query(self, sql: str, limit: int):
        self.query_limit = limit
        return {"sql": sql, "limit": limit}

    def explain_query(self, sql: str, include_plan: bool = False):
        self.include_plan = include_plan
        return {"sql": sql, "executed": False}


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


if __name__ == "__main__":
    unittest.main()
