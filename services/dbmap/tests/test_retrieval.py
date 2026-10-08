import gc
import json
import sys
import unittest
import weakref
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.config import Settings
from dbmap.graph import GraphEngine
from dbmap.retrieval import RetrievalIndex
from dbmap.service import DatabaseMapService


def sample_metadata():
    return {
        "relations": [
            {"schema": "public", "name": "employees", "kind": "table"},
            {"schema": "public", "name": "finance_profiles", "kind": "table"},
            {"schema": "public", "name": "announcements", "kind": "table"},
        ],
        "columns": [
            {"schema": "public", "table": "employees", "name": "id", "data_type": "integer"},
            {"schema": "public", "table": "employees", "name": "full_name", "data_type": "text"},
            {"schema": "public", "table": "finance_profiles", "name": "id", "data_type": "integer"},
            {"schema": "public", "table": "finance_profiles", "name": "employee_id", "data_type": "integer"},
            {"schema": "public", "table": "finance_profiles", "name": "current_ctc_amount", "data_type": "numeric"},
            {"schema": "public", "table": "announcements", "name": "id", "data_type": "integer"},
            {"schema": "public", "table": "announcements", "name": "title", "data_type": "text"},
        ],
        "constraints": [
            {
                "schema": "public",
                "table": "finance_profiles",
                "name": "finance_profiles_employee_id_fkey",
                "type": "FOREIGN KEY",
                "columns": ["employee_id"],
                "foreign_schema": "public",
                "foreign_table": "employees",
                "foreign_columns": ["id"],
            }
        ],
        "indexes": [],
        "dependencies": [],
        "usage": [],
        "usage_status": "disabled",
    }


def build_index():
    return RetrievalIndex(GraphEngine("testdb").build(sample_metadata()))


class TermExpansionTests(unittest.TestCase):
    def test_plurals_are_matched_without_being_reported_as_typos(self):
        index = build_index()

        terms, fuzzy, acronyms = index._expanded_terms("employees")

        self.assertIn("employee", terms)
        self.assertEqual(fuzzy, {})
        self.assertEqual(acronyms, {})

    def test_real_misspelling_is_still_corrected(self):
        index = build_index()

        _, fuzzy, _ = index._expanded_terms("anouncements")

        self.assertTrue(str(fuzzy.get("anouncements")).startswith("announcement"))

    def test_short_stopwords_do_not_produce_expansions(self):
        index = build_index()

        terms, fuzzy, acronyms = index._expanded_terms("what is all of the")

        self.assertEqual(terms, [])
        self.assertEqual(fuzzy, {})
        self.assertEqual(acronyms, {})

    def test_single_confident_match_still_includes_foreign_key_neighbour(self):
        index = build_index()

        context = index.context("current ctc amount", max_relations=6)
        ids = {item["id"] for item in context["relations"]}

        self.assertIn("table:public.finance_profiles", ids)
        self.assertIn("table:public.employees", ids)


class ContextCacheTests(unittest.TestCase):
    def test_repeat_call_is_cached_but_returns_independent_objects(self):
        index = build_index()

        first = index.context("employees", max_relations=6)
        second = index.context("employees", max_relations=6)
        first["relations"].clear()

        self.assertIsNot(first, second)
        self.assertTrue(index.context("employees", max_relations=6)["relations"])

    def test_discarded_index_is_collectable(self):
        index = build_index()
        index.context("employees")
        reference = weakref.ref(index)

        del index
        gc.collect()

        self.assertIsNone(reference())


class DeliveryWindowTests(unittest.TestCase):
    class Introspector:
        def __init__(self, snapshot):
            self.snapshot_value = snapshot

        def snapshot(self, refresh: bool = False):
            return self.snapshot_value

        def connectivity_check(self):
            return {"ok": True}

    def _service(self, directory: str, window: int) -> DatabaseMapService:
        snapshot = GraphEngine("testdb").build(sample_metadata())
        settings = Settings(
            database_url=None,
            host="localhost",
            port=5432,
            database="testdb",
            user="reader",
            password="",
            sslmode="prefer",
            cache_dir=Path(directory),
            statement_timeout_ms=5000,
            max_query_rows=200,
            api_host="127.0.0.1",
            api_port=8000,
            context_window=window,
            runtime_file=Path(directory) / "runtime.json",
        )
        return DatabaseMapService(self.Introspector(snapshot), settings=settings)

    def test_disabled_window_repeats_full_detail(self):
        with TemporaryDirectory() as directory:
            service = self._service(directory, 0)

            first = service.task_context("employees")
            second = service.task_context("employees")

            self.assertNotIn("known", first)
            self.assertNotIn("known", second)
            self.assertEqual(len(first["relations"]), len(second["relations"]))

    def test_second_task_reuses_already_delivered_relations(self):
        with TemporaryDirectory() as directory:
            service = self._service(directory, 16)

            service.task_context("employees")
            repeat = service.task_context("employees")

            self.assertEqual(repeat["relations"], [])
            self.assertIn("table:public.employees", repeat["known"])
            self.assertEqual(repeat["window"]["size"], 16)

    def test_refresh_context_restores_full_detail(self):
        with TemporaryDirectory() as directory:
            service = self._service(directory, 16)

            service.task_context("employees")
            refreshed = service.task_context("employees", refresh_context=True)

            self.assertTrue(refreshed["relations"])
            self.assertNotIn("known", refreshed)

    def test_new_columns_are_sent_as_a_partial_delta(self):
        with TemporaryDirectory() as directory:
            service = self._service(directory, 16)

            service.task_context("employees")
            service._window["table:public.finance_profiles"] = {"employee_id"}
            delta = service.task_context("current ctc amount")

            partial = [item for item in delta["relations"] if item.get("partial")]
            self.assertTrue(partial)
            self.assertNotIn("employee_id", partial[0]["columns"])

    def test_schema_change_clears_the_window(self):
        with TemporaryDirectory() as directory:
            service = self._service(directory, 16)
            service.task_context("employees")

            service._fingerprint = "a-different-schema"
            reset = service.task_context("employees")

            self.assertTrue(reset["relations"])
            self.assertNotIn("known", reset)

    def test_runtime_file_overrides_the_configured_size(self):
        with TemporaryDirectory() as directory:
            service = self._service(directory, 0)

            self.assertEqual(service.context_window_size(), 0)
            report = service.set_context_window(8)

            self.assertEqual(report["size"], 8)
            self.assertEqual(service.context_window_size(), 8)
            stored = json.loads((Path(directory) / "runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["context_window"], 8)

    def test_report_states_the_cost_of_enabling_the_window(self):
        with TemporaryDirectory() as directory:
            report = self._service(directory, 16).context_window_report()

            self.assertIn("refresh_context", report["cost"])
            self.assertIn("compacted", report["cost"])


if __name__ == "__main__":
    unittest.main()
