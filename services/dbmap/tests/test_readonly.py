import unittest
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.readonly import apply_limit, validate_readonly_sql


class ReadonlySqlTests(unittest.TestCase):
    def test_validate_allows_select(self):
        self.assertEqual(
            validate_readonly_sql("select * from public.customers"),
            "select * from public.customers",
        )


    def test_apply_limit_adds_limit(self):
        self.assertTrue(apply_limit("select * from public.customers", 10).endswith("LIMIT 10"))


    def test_apply_limit_keeps_existing_limit(self):
        self.assertTrue(apply_limit("select * from public.customers limit 5", 10).endswith("limit 5"))


    def test_validate_blocks_writes(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("delete from public.customers")
