from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.explain import explain_object
from dbmap.graph import GraphEngine


class ExplainObjectTests(unittest.TestCase):
    def test_explanation_includes_catalog_evidence_and_key_roles(self):
        snapshot = GraphEngine("testdb").build(
            {
                "relations": [
                    {
                        "schema": "public",
                        "name": "customers",
                        "kind": "table",
                        "comment": "Canonical customer records.",
                        "row_estimate": 12,
                    }
                ],
                "columns": [
                    {
                        "schema": "public",
                        "table": "customers",
                        "name": "id",
                        "data_type": "bigint",
                        "is_nullable": "NO",
                    }
                ],
                "constraints": [
                    {
                        "schema": "public",
                        "table": "customers",
                        "name": "customers_pkey",
                        "type": "PRIMARY KEY",
                        "columns": ["id"],
                    }
                ],
                "indexes": [],
            }
        )

        result = explain_object(snapshot, "table:public.customers")

        self.assertEqual(result["columns"][0]["key_roles"], ["PRIMARY KEY"])
        self.assertEqual(
            result["semantic_context"]["evidence"][0]["kind"],
            "database_comment",
        )
        self.assertEqual(
            result["semantic_context"]["source_of_truth_assessment"]["status"],
            "unverified",
        )


if __name__ == "__main__":
    unittest.main()
