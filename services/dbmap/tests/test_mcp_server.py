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


if __name__ == "__main__":
    unittest.main()
