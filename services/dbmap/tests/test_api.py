import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap import api


class FailingService:
    def connectivity_check(self):
        raise RuntimeError("password for private-db.example.com was rejected")


class ExplainService:
    def explain_query(self, sql: str, include_plan: bool = False):
        return {"sql": sql, "include_plan": include_plan, "executed": False}


class ApiTests(unittest.TestCase):
    def test_failed_health_check_returns_503_without_connection_details(self):
        with (
            patch.object(api, "service", FailingService()),
            patch.object(api.logger, "exception"),
        ):
            response = api.health()

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(payload, {"ok": False, "error": "PostgreSQL is unavailable."})
        self.assertNotIn("private-db.example.com", response.body.decode())

    def test_query_explain_delegates_without_execution(self):
        request = api.ExplainQueryRequest(sql="select 1", include_plan=False)
        with patch.object(api, "service", ExplainService()):
            response = api.query_explain(request)

        self.assertEqual(response["sql"], "select 1")
        self.assertFalse(response["executed"])


if __name__ == "__main__":
    unittest.main()
