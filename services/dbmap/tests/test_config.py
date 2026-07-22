import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.config import Settings


class SettingsTests(unittest.TestCase):
    def test_explicit_env_file_overrides_inherited_database_url(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "DATABASE_URL=postgresql://reader:file-password@db.example.com:5432/real_db\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "DBMAP_ENV_FILE": str(env_file),
                    "DATABASE_URL": "postgresql://reader:old-password@inherited.example.com:5432/old_db",
                },
                clear=False,
            ):
                settings = Settings.from_env()

        self.assertEqual(
            settings.database_url,
            "postgresql://reader:file-password@db.example.com:5432/real_db",
        )

    def test_database_url_label_and_cache_identity_do_not_expose_password(self):
        settings = Settings(
            database_url="postgresql://reader:secret@db.example.com:5433/production",
            host="ignored",
            port=5432,
            database="ignored",
            user="ignored",
            password="ignored",
            sslmode="require",
            cache_dir=Path(".dbmap-cache"),
            statement_timeout_ms=5000,
            max_query_rows=200,
            api_host="127.0.0.1",
            api_port=8000,
        )

        self.assertEqual(settings.safe_database_label(), "db.example.com:5433/production")
        self.assertEqual(settings.cache_identity(), "db.example.com:5433/production|reader")
        self.assertNotIn("secret", settings.cache_identity())

    def test_non_loopback_api_binding_is_rejected(self):
        settings = Settings(
            database_url=None,
            host="localhost",
            port=5432,
            database="app",
            user="reader",
            password="secret",
            sslmode="require",
            cache_dir=Path(".dbmap-cache"),
            statement_timeout_ms=5000,
            max_query_rows=200,
            api_host="0.0.0.0",
            api_port=8000,
        )

        with self.assertRaises(ValueError):
            settings.require_local_api()


if __name__ == "__main__":
    unittest.main()
