import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap import api
from dbmap.models import GraphSnapshot
from dbmap.security import ApiKeyAuth, Principal


class FailingService:
    def connectivity_check(self):
        raise RuntimeError("password for private-db.example.com was rejected")


class ExplainService:
    def explain_query(self, sql: str, include_plan: bool = False, actor: str = "local"):
        return {"sql": sql, "include_plan": include_plan, "executed": False}


class ReadonlyService:
    def readonly_query(
        self,
        sql: str,
        limit: int = 200,
        approved: bool = False,
        actor: str = "local",
    ):
        return {"sql": sql, "approved": approved, "actor": actor}

    def readonly_batch(self, queries, **kwargs):
        return {"queries": queries, **kwargs}


class TaskContextService:
    def task_context(self, question, **kwargs):
        return {"question": question, **kwargs}


class GraphService:
    audit = None

    def graph_snapshot(self, **kwargs):
        return GraphSnapshot.create("testdb", [], [])


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
            response = api.query_explain(request, Principal("tester", "analyst"))

        self.assertEqual(response["sql"], "select 1")
        self.assertFalse(response["executed"])

    def test_only_admin_can_approve_policy_exception(self):
        request = api.ReadonlyQueryRequest(sql="select 1", approved=True)
        with patch.object(api, "service", ReadonlyService()):
            with self.assertRaises(HTTPException) as denied:
                api.query_readonly(request, Principal("analyst", "data_reader"))
            allowed = api.query_readonly(request, Principal("dba", "admin"))

        self.assertEqual(denied.exception.status_code, 403)
        self.assertTrue(allowed["approved"])
        self.assertEqual(allowed["actor"], "dba")

    def test_batch_approval_and_task_context_delegate(self):
        batch_request = api.ReadonlyBatchRequest(
            queries=[api.BatchQuery(name="count", sql="select 1")],
            approved=True,
        )
        context_request = api.TaskContextRequest(question="customer orders")
        with patch.object(api, "service", ReadonlyService()):
            with self.assertRaises(HTTPException):
                api.query_readonly_batch(batch_request, Principal("reader", "data_reader"))
            batch = api.query_readonly_batch(batch_request, Principal("dba", "admin"))
        with patch.object(api, "service", TaskContextService()):
            context = api.workflow_task_context(
                context_request,
                Principal("analyst", "analyst"),
            )

        self.assertEqual(batch["queries"][0]["name"], "count")
        self.assertEqual(context["question"], "customer orders")

    def test_only_admin_can_change_the_shared_context_window(self):
        class WindowService:
            audit = None

            def context_window_report(self):
                return {"size": 0, "enabled": False}

            def set_context_window(self, size, actor="local"):
                return {"size": size, "enabled": size > 0, "actor": actor}

        request = api.ContextWindowRequest(size=16)
        with patch.object(api, "service", WindowService()):
            with self.assertRaises(HTTPException) as denied:
                api.set_context_window(request, Principal("analyst", "analyst"))
            allowed = api.set_context_window(request, Principal("dba", "admin"))
            report = api.get_context_window(Principal("viewer", "viewer"))

        self.assertEqual(denied.exception.status_code, 403)
        self.assertEqual(allowed["size"], 16)
        self.assertFalse(report["enabled"])

    def test_graph_endpoint_requires_valid_key_when_auth_is_enabled(self):
        token = "browser-test-key"
        with TemporaryDirectory() as directory:
            auth_file = Path(directory) / "auth.json"
            auth_file.write_text(
                json.dumps(
                    {
                        "users": [
                            {
                                "name": "viewer",
                                "role": "viewer",
                                "key_sha256": hashlib.sha256(token.encode()).hexdigest(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(api, "auth", ApiKeyAuth(auth_file, required=True)),
                patch.object(api, "service", GraphService()),
                TestClient(api.app) as client,
            ):
                denied = client.get("/graph")
                allowed = client.get(
                    "/graph",
                    headers={"Authorization": f"Bearer {token}"},
                )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
