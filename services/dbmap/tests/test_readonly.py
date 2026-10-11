import sys
import unittest
from pathlib import Path

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
        guarded = apply_limit("select * from public.customers limit 5", 10)

        self.assertIn("select * from public.customers limit 5", guarded)
        self.assertTrue(guarded.endswith("LIMIT 10"))

    def test_apply_limit_clamps_large_existing_limit(self):
        guarded = apply_limit("select * from public.customers limit 50000", 200)

        self.assertIn("limit 50000", guarded)
        self.assertTrue(guarded.endswith("LIMIT 200"))

    def test_apply_limit_clamps_limit_all(self):
        guarded = apply_limit("select * from public.customers limit all", 200)

        self.assertIn("limit all", guarded)
        self.assertTrue(guarded.endswith("LIMIT 200"))


    def test_validate_blocks_writes(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("delete from public.customers")

    def test_validate_blocks_select_into(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("select * into copied_customers from public.customers")

    def test_validate_blocks_privileged_functions(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("select pg_read_file('/etc/passwd')")

    def test_keywords_inside_string_literals_and_comments_are_ignored(self):
        allowed = [
            "select * from audit_log where action = 'delete'",
            "select * from audit_log where action in ('update', 'insert') -- drop the rest",
            "select * from t where note = 'it''s; fine' /* create */",
            "select 'pg_sleep(5)' as label",
            "select $1::text as p from t",
        ]
        for sql in allowed:
            with self.subTest(sql=sql):
                self.assertEqual(validate_readonly_sql(sql), sql)

    def test_ambiguous_literals_keep_the_strict_check(self):
        refused = [
            # Backslashes, E'' strings and dollar quotes are not stripped.
            "select 'a\\' delete from t",
            "select e'delete' from t",
            "select $$delete$$ from t",
            "select 'unterminated delete",
            "select 1 /* unterminated delete",
            "select 1; delete from t -- 'x'",
            "select '1'; select 2",
        ]
        for sql in refused:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_readonly_sql(sql)

    def test_sleep_and_quoted_function_names_are_blocked(self):
        for sql in [
            "select pg_sleep(30)",
            "select pg_catalog.pg_sleep_for('5 minutes')",
            'select "pg_sleep"(30)',
            "select pg_sleep /* x */ (1)",
            'select "pg_read_file"(\'postgresql.conf\')',
        ]:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_readonly_sql(sql)

    def test_validate_blocks_row_locks(self):
        with self.assertRaises(ValueError):
            validate_readonly_sql("select id from public.customers for update")
