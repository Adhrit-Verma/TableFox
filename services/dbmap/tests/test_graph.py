import unittest
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.explain import explain_object
from dbmap.graph import GraphEngine
from dbmap.search import search_snapshot


def sample_metadata():
    return {
        "relations": [
            {"schema": "public", "name": "customers", "kind": "table", "comment": "Buying accounts"},
            {"schema": "public", "name": "orders", "kind": "table", "comment": "Customer orders"},
        ],
        "columns": [
            {"schema": "public", "table": "customers", "name": "id", "data_type": "uuid", "is_nullable": "NO"},
            {"schema": "public", "table": "customers", "name": "email", "data_type": "text", "is_nullable": "NO"},
            {"schema": "public", "table": "orders", "name": "id", "data_type": "uuid", "is_nullable": "NO"},
            {"schema": "public", "table": "orders", "name": "customer_id", "data_type": "uuid", "is_nullable": "NO"},
        ],
        "constraints": [
            {
                "schema": "public",
                "table": "customers",
                "name": "customers_pkey",
                "type": "PRIMARY KEY",
                "columns": ["id"],
            },
            {
                "schema": "public",
                "table": "orders",
                "name": "orders_customer_id_fkey",
                "type": "FOREIGN KEY",
                "columns": ["customer_id"],
                "foreign_schema": "public",
                "foreign_table": "customers",
                "foreign_columns": ["id"],
            },
        ],
        "indexes": [
            {
                "schema": "public",
                "table": "customers",
                "name": "customers_email_idx",
                "is_unique": True,
                "is_primary": False,
                "columns": ["email"],
            }
        ],
        "dependencies": [],
        "usage": [],
        "usage_status": "disabled",
    }


class GraphEngineTests(unittest.TestCase):
    def test_build_graph_contains_core_nodes_and_foreign_key(self):
        snapshot = GraphEngine("testdb").build(sample_metadata())
        node_ids = {node.id for node in snapshot.nodes}
        edge_kinds = {edge.kind for edge in snapshot.edges}

        self.assertIn("schema:public", node_ids)
        self.assertIn("table:public.customers", node_ids)
        self.assertIn("column:public.orders.customer_id", node_ids)
        self.assertIn("foreign_key", edge_kinds)
        self.assertEqual(snapshot.summary["table"], 2)

    def test_search_finds_comments_and_columns(self):
        snapshot = GraphEngine("testdb").build(sample_metadata())
        results = search_snapshot(snapshot, "buying")
        self.assertEqual(results[0]["id"], "table:public.customers")

        column_results = search_snapshot(snapshot, "customer_id")
        self.assertEqual(column_results[0]["kind"], "column")

    def test_search_prioritizes_exact_relation_name_over_matching_columns(self):
        metadata = sample_metadata()
        metadata["columns"].append(
            {
                "schema": "public",
                "table": "orders",
                "name": "customers_archive_id",
                "data_type": "uuid",
                "is_nullable": "YES",
            }
        )
        snapshot = GraphEngine("testdb").build(metadata)

        results = search_snapshot(snapshot, "customers")

        self.assertEqual(results[0]["id"], "table:public.customers")

    def test_neighbors_returns_bounded_subgraph(self):
        snapshot = GraphEngine("testdb").build(sample_metadata())
        neighbors = GraphEngine.neighbors(snapshot, "table:public.orders", depth=1, max_nodes=4)
        self.assertLessEqual(len(neighbors.nodes), 4)
        self.assertTrue(any(node.id == "table:public.orders" for node in neighbors.nodes))

    def test_graph_bounds_reject_unbounded_negative_values(self):
        snapshot = GraphEngine("testdb").build(sample_metadata())

        filtered = GraphEngine.filter_snapshot(snapshot, max_nodes=-10)
        neighbors = GraphEngine.neighbors(
            snapshot,
            "table:public.orders",
            depth=-1,
            max_nodes=-10,
        )

        self.assertEqual(len(filtered.nodes), 1)
        self.assertEqual(len(neighbors.nodes), 1)

    def test_bounded_snapshot_keeps_schema_and_relation_context(self):
        snapshot = GraphEngine("testdb").build(sample_metadata())

        filtered = GraphEngine.filter_snapshot(snapshot, max_nodes=4)
        node_ids = {node.id for node in filtered.nodes}

        self.assertIn("schema:public", node_ids)
        self.assertIn("table:public.customers", node_ids)
        self.assertIn("table:public.orders", node_ids)
        self.assertTrue(
            all(not node.parent_id or node.parent_id in node_ids for node in filtered.nodes)
        )

    def test_schema_filter_excludes_unselected_schema_nodes(self):
        metadata = sample_metadata()
        metadata["relations"].append(
            {"schema": "audit", "name": "events", "kind": "table"}
        )
        snapshot = GraphEngine("testdb").build(metadata)

        filtered = GraphEngine.filter_snapshot(snapshot, schemas=["public"])

        self.assertNotIn("schema:audit", {node.id for node in filtered.nodes})

    def test_explain_object_includes_relationship_summary(self):
        snapshot = GraphEngine("testdb").build(sample_metadata())
        explanation = explain_object(snapshot, "table:public.orders")
        self.assertTrue(explanation["found"])
        self.assertTrue(explanation["columns"])
        self.assertEqual(explanation["foreign_keys_to"][0]["id"], "table:public.customers")

    def test_join_path_uses_declared_foreign_keys(self):
        snapshot = GraphEngine("testdb").build(sample_metadata())

        path = GraphEngine.join_path(
            snapshot,
            "table:public.orders",
            "table:public.customers",
        )

        self.assertTrue(path["found"])
        self.assertTrue(path["verified"])
        self.assertEqual(path["edges"][0]["kind"], "foreign_key")

    def test_usage_and_view_dependencies_are_added_to_graph(self):
        metadata = sample_metadata()
        metadata["relations"].append(
            {"schema": "public", "name": "customer_orders", "kind": "view"}
        )
        metadata["usage"] = [
            {
                "schema": "public",
                "name": "orders",
                "sequential_scans": 3,
                "index_scans": 8,
                "live_rows": 20,
                "last_analyze": None,
                "stats_reset": None,
            }
        ]
        metadata["usage_status"] = "available"
        metadata["dependencies"] = [
            {
                "schema": "public",
                "name": "customer_orders",
                "target_schema": "public",
                "target_name": "orders",
            }
        ]

        snapshot = GraphEngine("testdb").build(metadata)
        order = next(node for node in snapshot.nodes if node.id == "table:public.orders")

        self.assertEqual(order.metadata["usage"]["index_scans"], 8)
        view = next(
            node for node in snapshot.nodes if node.id == "view:public.customer_orders"
        )
        self.assertEqual(view.metadata["usage"]["status"], "not_applicable")
        self.assertIn("depends_on", {edge.kind for edge in snapshot.edges})
