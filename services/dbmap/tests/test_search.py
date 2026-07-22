from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.models import GraphNode, GraphSnapshot
from dbmap.search import search_snapshot


class SearchTests(unittest.TestCase):
    def test_search_explains_ranking_and_missing_usage_telemetry(self):
        snapshot = GraphSnapshot.create(
            "testdb",
            [
                GraphNode(
                    "table:public.customers",
                    "table",
                    "public.customers",
                    "public",
                    "customers",
                    metadata={"comment": "Customer source records"},
                )
            ],
            [],
        )

        result = search_snapshot(snapshot, "customers")[0]

        self.assertIn("exact_name", result["ranking"]["reasons"])
        self.assertEqual(result["ranking"]["usage_telemetry"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
