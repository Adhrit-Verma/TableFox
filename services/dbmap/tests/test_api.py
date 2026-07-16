import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap import api


class FailingIntrospector:
    def connectivity_check(self):
        raise RuntimeError("password for private-db.example.com was rejected")


class ApiTests(unittest.TestCase):
    def test_failed_health_check_returns_503_without_connection_details(self):
        with (
            patch.object(api, "introspector", FailingIntrospector()),
            patch.object(api.logger, "exception"),
        ):
            response = api.health()

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(payload, {"ok": False, "error": "PostgreSQL is unavailable."})
        self.assertNotIn("private-db.example.com", response.body.decode())


if __name__ == "__main__":
    unittest.main()
