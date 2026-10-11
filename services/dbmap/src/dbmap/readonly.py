from __future__ import annotations

import re

_SELECT_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE | re.DOTALL)
_BLOCKED = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|vacuum|analyze|refresh|merge|into)\b",
    re.IGNORECASE,
)
# A quoted name ("pg_sleep"(1)) calls the same function, so the quotes are optional here.
_BLOCKED_FUNCTION = re.compile(
    r'(?<![\w$])"?(pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|'
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|"
    r"pg_advisory_lock|pg_try_advisory_lock|pg_advisory_xact_lock|"
    r"pg_try_advisory_xact_lock|lo_import|lo_export|nextval|setval|set_config|"
    r'pg_sleep|pg_sleep_for|pg_sleep_until)"?\s*\(',
    re.IGNORECASE,
)
_LOCKING_CLAUSE = re.compile(r"\bfor\s+(update|no\s+key\s+update|share|key\s+share)\b", re.IGNORECASE)


def strip_literals(sql: str) -> str:
    """Return the SQL with plain string literals emptied and comments blanked out.

    Lets the keyword checks below ignore `action = 'delete'` or `-- update`. Where PostgreSQL
    could read a literal differently from this scanner (backslashes, E'' strings, dollar
    quotes, an unterminated literal or comment), the SQL is returned unchanged so the
    checks stay as strict as before. Double-quoted identifiers are kept as written.
    """
    out: list[str] = []
    index, length = 0, len(sql)
    while index < length:
        char = sql[index]
        pair = sql[index : index + 2]
        if pair == "--":
            end = sql.find("\n", index)
            out.append(" ")
            index = length if end < 0 else end
        elif pair == "/*":
            depth, index = 1, index + 2
            while depth:
                if index >= length:
                    return sql
                if sql.startswith("/*", index):
                    depth, index = depth + 1, index + 2
                elif sql.startswith("*/", index):
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
            out.append(" ")
        elif char == "'":
            if index and sql[index - 1] in "eE":
                return sql
            end = index + 1
            while True:
                end = sql.find("'", end)
                if end < 0:
                    return sql
                if sql.startswith("''", end):
                    end += 2
                    continue
                break
            if "\\" in sql[index:end]:
                return sql
            out.append("''")
            index = end + 1
        elif char == '"':
            end = index + 1
            while True:
                end = sql.find('"', end)
                if end < 0:
                    return sql
                if sql.startswith('""', end):
                    end += 2
                    continue
                break
            out.append(sql[index : end + 1])
            index = end + 1
        elif char == "$" and not (index and (sql[index - 1].isalnum() or sql[index - 1] in "_$")):
            if not sql[index + 1 : index + 2].isdigit():
                return sql  # a dollar-quoted string
            out.append(char)
            index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)


def validate_readonly_sql(sql: str) -> str:
    statement = sql.strip()
    if not statement:
        raise ValueError("SQL query is empty.")
    code = strip_literals(statement).strip()
    if ";" in code.rstrip(";"):
        raise ValueError("Only one SQL statement is allowed.")
    if not _SELECT_START.search(code):
        raise ValueError("Only SELECT or WITH queries are allowed.")
    if _BLOCKED.search(code):
        raise ValueError("Query contains a blocked write or maintenance keyword.")
    if _BLOCKED_FUNCTION.search(code):
        raise ValueError("Query contains a blocked privileged or state-changing function.")
    if _LOCKING_CLAUSE.search(code):
        raise ValueError("Row-locking SELECT queries are not allowed.")
    return statement.rstrip(";")


def apply_limit(sql: str, limit: int) -> str:
    statement = validate_readonly_sql(sql)
    row_limit = max(1, limit)
    return f"SELECT * FROM (\n{statement}\n) AS dbmap_readonly_query\nLIMIT {row_limit}"
