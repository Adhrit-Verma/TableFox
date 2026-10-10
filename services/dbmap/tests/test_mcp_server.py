import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.mcp_server import create_mcp


class McpServerTests(unittest.TestCase):
    def test_expected_tools_are_registered(self):
        tools = asyncio.run(create_mcp().list_tools())
        names = {tool.name for tool in tools}

        self.assertEqual(
            names,
            {
                "database_connectivity_check",
                "database_context_identity",
                "database_context_window",
                "database_explain_object",
                "database_explain_query",
                "database_find_join_path",
                "database_graph_snapshot",
                "database_neighbors",
                "database_readonly_query",
                "database_readonly_batch",
                "database_schema_changes",
                "database_search",
                "database_source_of_truth",
                "database_task_context",
            },
        )

    def test_every_tool_has_explicit_annotations(self):
        for tool in asyncio.run(create_mcp().list_tools()):
            hints = tool.annotations
            self.assertIsNotNone(hints, tool.name)
            for value in (hints.read_only_hint, hints.destructive_hint, hints.open_world_hint):
                self.assertIsInstance(value, bool, tool.name)
            self.assertFalse(hints.destructive_hint, tool.name)

    def test_validation_errors_reach_the_model_as_text(self):
        from types import SimpleNamespace

        from mcp.server.mcpserver.exceptions import ToolError

        class Service:
            settings = SimpleNamespace(mcp_actor="test")

            def readonly_query(self, sql, limit, actor):
                raise ValueError("Only SELECT or WITH queries are allowed.")

        mcp = create_mcp(Service())
        with self.assertRaisesRegex(ToolError, "Only SELECT or WITH queries are allowed"):
            asyncio.run(mcp.call_tool("database_readonly_query", {"sql": "delete from t"}))

    def test_batch_schema_tells_the_model_how_to_name_queries(self):
        tools = {tool.name: tool for tool in asyncio.run(create_mcp().list_tools())}
        schema = str(tools["database_readonly_batch"].input_schema)
        self.assertIn("A-Za-z0-9_-", schema)
        self.assertIn("sql", schema)

    def test_site_pages_are_served(self):
        from starlette.testclient import TestClient

        client = TestClient(create_mcp(public_url="https://x").streamable_http_app())
        home = client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Ask your PostgreSQL database anything", home.text)
        self.assertIn("frame-ancestors 'none'", home.headers["content-security-policy"])
        for path, text in (("/privacy", "What TableFox stores"), ("/terms", "No warranty")):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(text, response.text)


if __name__ == "__main__":
    unittest.main()
