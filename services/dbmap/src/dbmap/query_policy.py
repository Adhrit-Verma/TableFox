from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

MAX_PLAN_NODES = 1000
MAX_PLAN_BYTES = 250_000

_SENSITIVE_MARKERS = {
    "credential": {
        "access_token",
        "api_key",
        "api_secret",
        "auth_token",
        "client_secret",
        "key_hash",
        "key_prefix",
        "otp_secret",
        "passwd",
        "password",
        "password_hash",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "session_token",
        "token",
    },
    "personal_data": {
        "address",
        "birth_date",
        "birthdate",
        "date_of_birth",
        "dob",
        "driver_license",
        "drivers_license",
        "email",
        "email_address",
        "mobile",
        "national_id",
        "passport",
        "passport_number",
        "phone",
        "phone_number",
        "social_security_number",
        "ssn",
        "tax_code",
        "tax_id",
        "tax_number",
    },
    "financial": {
        "bank_account",
        "card_last4",
        "card_number",
        "compensation",
        "cvc",
        "cvv",
        "iban",
        "last4",
        "pan",
        "routing_number",
        "salaries",
        "salary",
        "sort_code",
        "wage",
        "wages",
    },
}
# Opt-in (DBMAP_BLOCK_IP_ADDRESSES): IP addresses are personal data in some jurisdictions but
# are also the everyday subject of log and security questions.
_NETWORK_MARKERS = {"client_ip", "ip", "ip_addr", "ip_address", "ipv4", "ipv6", "remote_addr", "remote_ip"}
# "ip_address" and "mac_address" are not postal addresses.
_NOT_POSTAL = {"ip", "ipv4", "ipv6", "mac", "remote", "web", "wallet"}
# A name ending in one of these describes a sensitive value rather than holding it:
# api_key_id (a foreign key), password_changed_at, token_count, email_verified.
_DESCRIPTIVE_SUFFIXES = {
    "at", "changed", "cnt", "count", "created", "enabled", "expires", "id", "ids", "kind",
    "len", "length", "required", "set", "status", "ts", "type", "updated", "uuid", "valid",
    "verified", "version",
}
# Amount-like columns of a payroll or salary table are compensation even with neutral names.
_COMPENSATION_RELATIONS = {
    "compensation", "paycheck", "paychecks", "payroll", "payrolls", "payslip", "payslips",
    "salaries", "salary", "wage", "wages",
}
_COMPENSATION_COLUMNS = {
    "amount", "amt", "base", "bonus", "commission", "deduction", "deductions", "gross", "net",
    "overtime", "pay", "rate", "salary", "tax", "total", "wage",
}
_NOT_AMOUNT = _DESCRIPTIVE_SUFFIXES | {"currency", "date", "day", "frequency", "method", "month", "period", "year"}
# Plan nodes whose inputs EXPLAIN does not print; their plans are checked conservatively.
_OPAQUE_NODES = {"Custom Scan", "Named Tuplestore Scan", "Recursive Union", "Values Scan", "WorkTable Scan"}
# A relation scan's own output is exact only where it becomes a result directly; under a
# join or aggregate PostgreSQL may print the whole row (a "physical target list").
_EXACT_SCAN_PARENTS = {"InitPlan", "Member", "SubPlan", "Subquery"}
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_IDENTIFIER = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
_REFERENCE = re.compile(rf"(::\s*)?({_IDENTIFIER}(?:\s*\.\s*(?:{_IDENTIFIER}|\*))*)(\s*\()?")


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _marker_category(
    normalized: str,
    extra_markers: tuple[str, ...] = (),
    block_ip_addresses: bool = False,
) -> str | None:
    tokens = normalized.split("_")
    groups = list(_SENSITIVE_MARKERS.items())
    if extra_markers:
        groups.append(("configured", {_normalize(marker) for marker in extra_markers}))
    if block_ip_addresses:
        groups.append(("network_identifier", _NETWORK_MARKERS))
    for category, markers in groups:
        hits = [marker for marker in markers if marker and _contains_marker(normalized, marker)]
        if "address" in hits and set(tokens) & _NOT_POSTAL:
            hits.remove("address")
        if not hits:
            continue
        if tokens[-1] in _DESCRIPTIVE_SUFFIXES and not any(
            normalized == marker or normalized.endswith(f"_{marker}") for marker in hits
        ):
            continue
        return category
    return None


def classify_sensitive_columns(
    column_names: list[str],
    policy_classifications: dict[str, str] | None = None,
    *,
    extra_markers: tuple[str, ...] = (),
    block_ip_addresses: bool = False,
) -> list[dict[str, str]]:
    """Return conservative, name-based classifications of result-column names."""
    findings: list[dict[str, str]] = []
    configured = {key.lower(): value for key, value in (policy_classifications or {}).items()}
    for column_name in column_names:
        normalized = _normalize(column_name)
        if normalized in configured:
            findings.append(
                {
                    "column": column_name,
                    "category": configured[normalized],
                    "detection": "approved_context_policy",
                }
            )
            continue
        category = _marker_category(normalized, extra_markers, block_ip_addresses)
        if category:
            findings.append(
                {
                    "column": column_name,
                    "category": category,
                    "detection": "column_name_heuristic",
                }
            )
    return findings


def classify_source_columns(
    sources: set[tuple[str, str]],
    policy_classifications: dict[str, str] | None = None,
    *,
    extra_markers: tuple[str, ...] = (),
    block_ip_addresses: bool = False,
) -> list[dict[str, str]]:
    """Classify the table columns a query's results are computed from.

    `sources` comes from `plan_source_columns`, so an alias (`email AS contact`) or an
    expression (`lower(email)`) is judged by the column it reads, not the name it returns.
    """
    findings: list[dict[str, str]] = []
    configured = {key.lower(): value for key, value in (policy_classifications or {}).items()}
    for relation, column in sorted(sources):
        label = f"{relation}.{column}"
        normalized = _normalize(column)
        if column == "*":
            findings.append(
                {
                    "column": label,
                    "category": "unverified",
                    "detection": "whole_row_of_unknown_relation",
                }
            )
            continue
        if normalized in configured:
            findings.append(
                {"column": label, "category": configured[normalized], "detection": "approved_context_policy"}
            )
            continue
        category = _marker_category(normalized, extra_markers, block_ip_addresses)
        relation_tokens = set(_normalize(relation.rsplit(".", 1)[-1]).split("_"))
        if (
            category is None
            and relation_tokens & _COMPENSATION_RELATIONS
            and set(normalized.split("_")) & _COMPENSATION_COLUMNS
            and normalized.split("_")[-1] not in _NOT_AMOUNT
        ):
            category = "financial"
        if category:
            findings.append({"column": label, "category": category, "detection": "source_column_heuristic"})
    return findings


def plan_source_columns(
    payload: Any,
    relation_columns: dict[str, list[str]] | None = None,
) -> set[tuple[str, str]]:
    """Return the (relation, column) pairs that can reach a query's result rows.

    Reads the output lists of an EXPLAIN (VERBOSE, FORMAT JSON) plan. Every relation scan is
    mapped by its alias, so `c.email` resolves to the table behind `c` however the result is
    named. A whole-row reference (`to_jsonb(c)`, printed `c.*`) expands to every column.
    Scans under joins and aggregates are skipped because PostgreSQL may print their entire
    row there, but whatever they pass upward appears in the parent's output. Plans with
    nodes that hide their inputs (VALUES, recursive CTEs) include every scan instead.
    Columns used only in filters, join conditions or `count(*)` are not reported.
    """
    root = _plan_document(payload).get("Plan")
    if not isinstance(root, dict):
        return set()
    known = relation_columns or {}
    nodes: list[tuple[dict[str, Any], bool]] = []
    stack = [(root, True)]
    while stack and len(nodes) < MAX_PLAN_NODES:
        node, is_root = stack.pop()
        nodes.append((node, is_root))
        children = node.get("Plans", [])
        if isinstance(children, list):
            stack.extend((child, False) for child in children if isinstance(child, dict))
    truncated = bool(stack)

    aliases: dict[str, str] = {}
    for node, _ in nodes:
        label = _relation_label(node)
        if label:
            aliases[str(node.get("Alias") or node.get("Relation Name"))] = label
    conservative = truncated or any(str(node.get("Node Type")) in _OPAQUE_NODES for node, _ in nodes)

    expressions: list[str] = []
    for node, is_root in nodes:
        if (
            _relation_label(node)
            and not is_root
            and not conservative
            and node.get("Parent Relationship") not in _EXACT_SCAN_PARENTS
        ):
            continue
        output = node.get("Output")
        if isinstance(output, list):
            expressions.extend(str(item) for item in output)
        for key in ("Function Call", "Table Function Call"):
            if isinstance(node.get(key), str):
                expressions.append(node[key])

    relations = set(aliases.values())
    sources: set[tuple[str, str]] = set()
    for expression in expressions:
        for qualifier, column in _references(expression):
            if qualifier is None:
                for relation in relations:
                    if relation not in known or column in known[relation]:
                        sources.add((relation, column))
                continue
            relation = aliases.get(qualifier)
            if relation is None:
                # A subquery, CTE or function alias: its inputs are listed by its own nodes.
                continue
            if column == "*":
                if relation in known:
                    sources.update((relation, name) for name in known[relation])
                else:
                    sources.add((relation, "*"))
            else:
                sources.add((relation, column))
    return sources


def _references(expression: str) -> list[tuple[str | None, str]]:
    """Column references in one deparsed plan expression: (qualifier or None, column)."""
    text = _STRING_LITERAL.sub("''", expression)
    found: list[tuple[str | None, str]] = []
    for match in _REFERENCE.finditer(text):
        cast, reference, call = match.groups()
        if cast or call:
            continue
        parts = [_unquote(part.strip()) for part in re.findall(rf"{_IDENTIFIER}|\*", reference)]
        if len(parts) == 1:
            found.append((None, parts[0]))
        else:
            found.append((parts[-2], parts[-1]))
    return found


def _unquote(identifier: str) -> str:
    if len(identifier) >= 2 and identifier[0] == identifier[-1] == '"':
        return identifier[1:-1].replace('""', '"')
    return identifier


def assess_query_plan(
    payload: Any,
    *,
    max_total_cost: float,
    max_plan_rows: int,
    include_plan: bool = False,
    row_limit: int | None = None,
) -> dict[str, Any]:
    """Apply cost and row thresholds to an EXPLAIN plan.

    `row_limit` is the LIMIT TableFox wraps around every read. When it is within the row
    threshold, a large row estimate cannot reach the caller: `GROUP BY date_trunc(...)` is
    routinely estimated at hundreds of thousands of groups for a dozen-row answer, so the
    estimate is reported as a warning instead of a refusal. The cost threshold still applies.
    """
    document = _plan_document(payload)
    root = document.get("Plan")
    if not isinstance(root, dict):
        raise ValueError("PostgreSQL returned an invalid EXPLAIN plan.")  # noqa: TRY004 - callers handle ValueError

    node_types: Counter[str] = Counter()
    relations: set[str] = set()
    sequential_scans: set[str] = set()
    stack = [root]
    visited = 0
    truncated = False

    while stack:
        node = stack.pop()
        visited += 1
        if visited > MAX_PLAN_NODES:
            truncated = True
            break
        node_type = str(node.get("Node Type") or "Unknown")
        node_types[node_type] += 1
        relation = _relation_label(node)
        if relation:
            relations.add(relation)
            if node_type.endswith("Seq Scan"):
                sequential_scans.add(relation)
        children = node.get("Plans", [])
        if isinstance(children, list):
            stack.extend(child for child in children if isinstance(child, dict))

    total_cost = float(root.get("Total Cost") or 0)
    plan_rows = int(root.get("Plan Rows") or 0)
    blocking_reasons: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if total_cost > max_total_cost:
        blocking_reasons.append(
            {
                "code": "total_cost_exceeded",
                "value": total_cost,
                "limit": max_total_cost,
            }
        )
    if plan_rows > max_plan_rows and row_limit is not None and 0 < row_limit <= max_plan_rows:
        warnings.append(
            {
                "code": "estimated_rows_bounded_by_limit",
                "value": plan_rows,
                "row_limit": row_limit,
            }
        )
    elif plan_rows > max_plan_rows:
        blocking_reasons.append(
            {
                "code": "estimated_rows_exceeded",
                "value": plan_rows,
                "limit": max_plan_rows,
            }
        )
    if sequential_scans:
        warnings.append(
            {
                "code": "sequential_scan",
                "relations": sorted(sequential_scans),
                "note": "Sequential scans can be valid for small tables; review in context.",
            }
        )
    if truncated:
        warnings.append(
            {
                "code": "plan_summary_truncated",
                "limit": MAX_PLAN_NODES,
            }
        )

    result: dict[str, Any] = {
        "executed": False,
        "within_policy": not blocking_reasons,
        "approval_required": bool(blocking_reasons),
        "summary": {
            "root_node_type": str(root.get("Node Type") or "Unknown"),
            "total_cost": total_cost,
            "estimated_rows": plan_rows,
            "plan_nodes": min(visited, MAX_PLAN_NODES),
            "node_types": dict(sorted(node_types.items())),
            "relations": sorted(relations),
            "sequential_scans": sorted(sequential_scans),
        },
        "policy": {
            "max_total_cost": max_total_cost,
            "max_estimated_rows": max_plan_rows,
            "statement_executed": False,
            "explain_analyze": False,
        },
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }
    if include_plan:
        encoded = json.dumps(payload, default=str)
        if len(encoded.encode("utf-8")) <= MAX_PLAN_BYTES:
            result["plan"] = payload
        else:
            result["plan_omitted"] = f"Plan exceeded {MAX_PLAN_BYTES} bytes."
    return result


def _contains_marker(value: str, marker: str) -> bool:
    return value == marker or value.startswith(f"{marker}_") or value.endswith(f"_{marker}") or f"_{marker}_" in value


def _plan_document(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    if isinstance(payload, dict):
        return payload
    raise ValueError("PostgreSQL returned an invalid EXPLAIN response.")


def _relation_label(node: dict[str, Any]) -> str | None:
    relation = node.get("Relation Name")
    if not relation:
        return None
    schema = node.get("Schema")
    return f"{schema}.{relation}" if schema else str(relation)
