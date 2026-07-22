from __future__ import annotations

import re


_SELECT_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE | re.DOTALL)
_BLOCKED = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|vacuum|analyze|refresh|merge|into)\b",
    re.IGNORECASE,
)
_BLOCKED_FUNCTION = re.compile(
    r"\b(pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|"
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|"
    r"pg_advisory_lock|pg_try_advisory_lock|pg_advisory_xact_lock|"
    r"pg_try_advisory_xact_lock|lo_import|lo_export|nextval|setval|set_config)\s*\(",
    re.IGNORECASE,
)
_LOCKING_CLAUSE = re.compile(r"\bfor\s+(update|no\s+key\s+update|share|key\s+share)\b", re.IGNORECASE)


def validate_readonly_sql(sql: str) -> str:
    statement = sql.strip()
    if not statement:
        raise ValueError("SQL query is empty.")
    if ";" in statement.rstrip(";"):
        raise ValueError("Only one SQL statement is allowed.")
    if not _SELECT_START.search(statement):
        raise ValueError("Only SELECT or WITH queries are allowed.")
    if _BLOCKED.search(statement):
        raise ValueError("Query contains a blocked write or maintenance keyword.")
    if _BLOCKED_FUNCTION.search(statement):
        raise ValueError("Query contains a blocked privileged or state-changing function.")
    if _LOCKING_CLAUSE.search(statement):
        raise ValueError("Row-locking SELECT queries are not allowed.")
    return statement.rstrip(";")


def apply_limit(sql: str, limit: int) -> str:
    statement = validate_readonly_sql(sql)
    row_limit = max(1, limit)
    return f"SELECT * FROM (\n{statement}\n) AS dbmap_readonly_query\nLIMIT {row_limit}"
