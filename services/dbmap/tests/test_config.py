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


if __name__ == "__main__":
    unittest.main()
