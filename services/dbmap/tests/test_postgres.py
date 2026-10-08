import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.config import Settings
from dbmap.models import GraphSnapshot
from dbmap.postgres import PostgresIntrospector


def settings(database_url: str, cache_dir: Path) -> Settings:
    return Settings(
        database_url=database_url,
        host="ignored",
        port=5432,
        database="ignored",
        user="ignored",
        password="ignored",
        sslmode="require",
        cache_dir=cache_dir,
        statement_timeout_ms=5000,
        max_query_rows=200,
        api_host="127.0.0.1",
        api_port=8000,
    )


class PostgresIntrospectorTests(unittest.TestCase):
    def test_database_url_reaches_the_pool_as_conninfo(self):
        import psycopg_pool

        created = {}

        class Pool:
            def __init__(self, conninfo="", **options):
                created.update(conninfo=conninfo, kwargs=options["kwargs"])

            def open(self, wait):
                pass

            def connection(self):
                return None

            def close(self):
                pass

        real_pool = psycopg_pool.ConnectionPool
        psycopg_pool.ConnectionPool = Pool
        try:
            with TemporaryDirectory() as directory:
                PostgresIntrospector(settings("postgresql://reader@db.example.com/app", Path(directory)))._connection()
        finally:
            psycopg_pool.ConnectionPool = real_pool
        self.assertEqual(created["conninfo"], "postgresql://reader@db.example.com/app")
        self.assertNotIn("conninfo", created["kwargs"])

    def test_cache_key_does_not_change_when_password_rotates(self):
        with TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            first = PostgresIntrospector(
                settings("postgresql://reader:first@db.example.com/app", cache_dir)
            )
            second = PostgresIntrospector(
                settings("postgresql://reader:second@db.example.com/app", cache_dir)
            )

            self.assertEqual(first._cache_path(), second._cache_path())

    def test_cache_write_is_loadable_and_leaves_no_temporary_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            snapshot = GraphSnapshot.create("testdb", [], [])

            PostgresIntrospector._write_cache(path, snapshot)
            loaded = PostgresIntrospector._load_cache(path)

            self.assertEqual(loaded.database, "testdb")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_guarded_query_uses_one_connection_for_explain_and_execution(self):
        class Result:
            def fetchone(self):
                return ([{"Plan": {"Node Type": "Result", "Total Cost": 0, "Plan Rows": 1}}],)

        class Column:
            name = "value"

        class Cursor:
            description = (Column(),)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def execute(self, _sql):
                return self

            def __iter__(self):
                return iter([{"value": 1}])

        class Connection:
            def execute(self, sql):
                self.explains += sql.startswith("EXPLAIN")
                return Result()

            def cursor(self, **_kwargs):
                return Cursor()

            explains = 0

        introspector = PostgresIntrospector(settings("postgresql://reader@db/app", Path(".")))
        connection = Connection()
        checkouts = 0

        @contextmanager
        def checkout():
            nonlocal checkouts
            checkouts += 1
            yield connection

        introspector._connection = checkout
        introspector._configure_readonly_transaction = lambda _conn: None

        result = introspector.readonly_query("select 1 as value", limit=10)

        self.assertEqual(checkouts, 1)
        self.assertEqual(connection.explains, 1)
        self.assertEqual(result["rows"], [{"value": 1}])

    def test_batch_validation_and_response_budget(self):
        rows = [
            {
                "name": "values",
                "blocked": False,
                "columns": ["value"],
                "rows": [["x" * 500] for _ in range(10)],
                "row_count": 10,
            }
        ]

        result = PostgresIntrospector._bound_batch(rows, 2048)

        self.assertTrue(result["truncated"])
        self.assertLessEqual(result["bytes"], 2048)

    def test_batch_uses_one_checkout_and_compact_columnar_results(self):
        introspector = PostgresIntrospector(settings("postgresql://reader@db/app", Path(".")))
        checkouts = 0
        calls = []

        @contextmanager
        def checkout():
            nonlocal checkouts
            checkouts += 1
            yield object()

        def run(_connection, sql, limit, approved, _classifications):
            calls.append((sql, limit, approved))
            return {
                "blocked": False,
                "columns": ["value"],
                "rows": [{"value": len(calls)}],
                "row_count": 1,
                "plan": {"within_policy": True},
                "join_validation": {"verified": True},
                "sensitive_columns": [],
            }

        introspector._connection = checkout
        introspector._configure_readonly_transaction = lambda _connection: None
        introspector._context_classifications = dict
        introspector._readonly_query_on_connection = run

        result = introspector.readonly_batch(
            [
                {"name": "first", "sql": "select 1"},
                {"name": "second", "sql": "select 2"},
            ],
            max_rows_each=5,
        )

        self.assertEqual(checkouts, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["results"][0]["rows"], [[1]])
        self.assertNotIn("policy", result["results"][0])

    def test_batch_rejects_writes_and_duplicate_names_before_connecting(self):
        introspector = PostgresIntrospector(settings("postgresql://reader@db/app", Path(".")))

        with self.assertRaises(ValueError):
            introspector.readonly_batch([{"name": "bad", "sql": "delete from users"}])
        with self.assertRaises(ValueError):
            introspector.readonly_batch(
                [
                    {"name": "same", "sql": "select 1"},
                    {"name": "same", "sql": "select 2"},
                ]
            )


if __name__ == "__main__":
    unittest.main()
