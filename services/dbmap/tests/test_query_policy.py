from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.query_policy import assess_query_plan, classify_sensitive_columns


class QueryPolicyTests(unittest.TestCase):
    def test_plan_thresholds_require_approval_without_executing_query(self):
        payload = [
            {
                "Plan": {
                    "Node Type": "Hash Join",
                    "Total Cost": 250000.5,
                    "Plan Rows": 400000,
                    "Plans": [
                        {
                            "Node Type": "Seq Scan",
                            "Schema": "public",
                            "Relation Name": "orders",
                            "Total Cost": 10,
                            "Plan Rows": 100,
                        }
                    ],
                }
            }
        ]

        result = assess_query_plan(
            payload,
            max_total_cost=100000,
            max_plan_rows=100000,
        )

        self.assertFalse(result["executed"])
        self.assertFalse(result["within_policy"])
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["summary"]["sequential_scans"], ["public.orders"])
        self.assertNotIn("plan", result)

    def test_sensitive_columns_are_classified_by_name(self):
        findings = classify_sensitive_columns(
            ["customer_id", "customer_email", "password_hash", "created_at"]
        )

        self.assertEqual(
            {(item["column"], item["category"]) for item in findings},
            {
                ("customer_email", "personal_data"),
                ("password_hash", "credential"),
            },
        )

    def test_approved_context_classification_takes_precedence(self):
        findings = classify_sensitive_columns(
            ["internal_reference"],
            {"internal_reference": "restricted"},
        )

        self.assertEqual(findings[0]["detection"], "approved_context_policy")
        self.assertEqual(findings[0]["category"], "restricted")


if __name__ == "__main__":
    unittest.main()
