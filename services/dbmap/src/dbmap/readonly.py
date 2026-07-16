from __future__ import annotations

import re


_SELECT_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE | re.DOTALL)
_BLOCKED = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|vacuum|analyze|refresh|merge|into)\b",
    re.IGNORECASE,
)


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
    return statement.rstrip(";")


def apply_limit(sql: str, limit: int) -> str:
    statement = validate_readonly_sql(sql)
    row_limit = max(1, limit)
    return f"SELECT * FROM (\n{statement}\n) AS dbmap_readonly_query\nLIMIT {row_limit}"
