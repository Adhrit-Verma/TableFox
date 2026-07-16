from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


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


if __name__ == "__main__":
    unittest.main()
