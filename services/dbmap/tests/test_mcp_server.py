import asyncio
from pathlib import Path
import sys
import unittest


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
                "database_explain_object",
                "database_explain_query",
                "database_find_join_path",
                "database_graph_snapshot",
                "database_neighbors",
                "database_readonly_query",
                "database_schema_changes",
                "database_search",
                "database_source_of_truth",
            },
        )


if __name__ == "__main__":
    unittest.main()
