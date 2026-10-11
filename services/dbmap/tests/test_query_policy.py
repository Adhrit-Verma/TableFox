import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbmap.query_policy import (
    assess_query_plan,
    classify_sensitive_columns,
    classify_source_columns,
    plan_source_columns,
)

COLUMNS = {
    "crm.contacts": ["contact_id", "account_id", "email", "phone", "name"],
    "crm.accounts": ["account_id", "name", "tax_id"],
    "hr.payroll": ["payroll_id", "employee_id", "net_amt", "pay_date"],
}


def scan(relation, output, alias=None, parent=None, schema="crm", **extra):
    node = {
        "Node Type": "Seq Scan",
        "Schema": schema,
        "Relation Name": relation,
        "Alias": alias or relation,
        "Output": output,
        **extra,
    }
    if parent:
        node["Parent Relationship"] = parent
    return node


def node(node_type, output, *children, parent=None, **extra):
    result = {"Node Type": node_type, "Output": output, "Plans": list(children), **extra}
    if parent:
        result["Parent Relationship"] = parent
    return result


def sources(plan):
    return plan_source_columns([{"Plan": plan}], COLUMNS)


def blocked(plan, **options):
    return {item["column"] for item in classify_source_columns(sources(plan), **options)}


class QueryPolicyTests(unittest.TestCase):
    def test_plan_thresholds_require_approval_without_executing_query(self):
        payload = [
            {
                "Plan": {
                    "Node Type": "Hash Join",
                    "Total Cost": 250000.5,
                    "Plan Rows": 400000,
                    "Plans": [
                        {
                            "Node Type": "Seq Scan",
                            "Schema": "public",
                            "Relation Name": "orders",
                            "Total Cost": 10,
                            "Plan Rows": 100,
                        }
                    ],
                }
            }
        ]

        result = assess_query_plan(
            payload,
            max_total_cost=100000,
            max_plan_rows=100000,
        )

        self.assertFalse(result["executed"])
        self.assertFalse(result["within_policy"])
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["summary"]["sequential_scans"], ["public.orders"])
        self.assertNotIn("plan", result)

    def test_row_estimate_is_bounded_by_the_applied_limit(self):
        # GROUP BY date_trunc(...) estimated at 300,000 groups, returned through LIMIT 200.
        payload = [{"Plan": {"Node Type": "HashAggregate", "Total Cost": 5000, "Plan Rows": 300000}}]

        bounded = assess_query_plan(payload, max_total_cost=100000, max_plan_rows=100000, row_limit=200)
        unbounded = assess_query_plan(payload, max_total_cost=100000, max_plan_rows=100000)
        too_large_limit = assess_query_plan(
            payload, max_total_cost=100000, max_plan_rows=100000, row_limit=500000
        )
        expensive = assess_query_plan(
            [{"Plan": {"Node Type": "HashAggregate", "Total Cost": 900000, "Plan Rows": 300000}}],
            max_total_cost=100000,
            max_plan_rows=100000,
            row_limit=200,
        )

        self.assertTrue(bounded["within_policy"])
        self.assertEqual(bounded["warnings"][0]["code"], "estimated_rows_bounded_by_limit")
        self.assertFalse(unbounded["within_policy"])
        self.assertFalse(too_large_limit["within_policy"])
        self.assertEqual(
            [reason["code"] for reason in expensive["blocking_reasons"]], ["total_cost_exceeded"]
        )

    def test_sensitive_columns_are_classified_by_name(self):
        findings = classify_sensitive_columns(
            ["customer_id", "customer_email", "password_hash", "created_at"]
        )

        self.assertEqual(
            {(item["column"], item["category"]) for item in findings},
            {
                ("customer_email", "personal_data"),
                ("password_hash", "credential"),
            },
        )

    def test_approved_context_classification_takes_precedence(self):
        findings = classify_sensitive_columns(
            ["internal_reference"],
            {"internal_reference": "restricted"},
        )

        self.assertEqual(findings[0]["detection"], "approved_context_policy")
        self.assertEqual(findings[0]["category"], "restricted")


    def test_identity_and_financial_markers(self):
        names = [
            "national_id", "iban", "bank_iban", "payer_iban", "card_last4", "tax_id", "tax_code",
            "salary_amt", "key_prefix", "passport_number", "birth_date", "card_number",
        ]
        flagged = {item["column"] for item in classify_sensitive_columns(names)}
        self.assertEqual(flagged, set(names))

    def test_names_that_describe_a_sensitive_value_are_not_flagged(self):
        names = [
            "api_key_id", "password_changed_at", "token_count", "email_verified", "phone_type",
            "ip_address", "client_ip", "mac_address", "order_id", "created_at",
        ]
        self.assertEqual(classify_sensitive_columns(names), [])

    def test_ip_addresses_and_custom_markers_are_opt_in(self):
        findings = classify_sensitive_columns(
            ["ip_address", "client_ip", "loyalty_number", "region"],
            block_ip_addresses=True,
            extra_markers=("loyalty_number",),
        )
        self.assertEqual(
            {(item["column"], item["category"]) for item in findings},
            {
                ("ip_address", "network_identifier"),
                ("client_ip", "network_identifier"),
                ("loyalty_number", "configured"),
            },
        )

    def test_alias_is_judged_by_the_source_column(self):
        # select email as contact from crm.contacts limit 3
        plan = node("Limit", ["email"], scan("contacts", ["email"], parent="Outer"))
        self.assertEqual(blocked(plan), {"crm.contacts.email"})

    def test_expression_is_judged_by_the_source_column(self):
        # select lower(email) from crm.contacts / select md5(c.email::text) ...
        self.assertEqual(blocked(scan("contacts", ["lower(email)"])), {"crm.contacts.email"})
        plan = scan("contacts", ["md5((c.email)::text)", "'c.phone'::text"], alias="c")
        self.assertEqual(blocked(plan), {"crm.contacts.email"})

    def test_quoted_alias_through_a_flattened_subquery(self):
        # select "Email" from (select email as "Email" from crm.contacts) z
        self.assertEqual(blocked(scan("contacts", ["contacts.email"])), {"crm.contacts.email"})

    def test_whole_row_references_expand_to_every_column(self):
        # select to_jsonb(c) from crm.contacts c
        self.assertEqual(
            blocked(scan("contacts", ["to_jsonb(c.*)"], alias="c")),
            {"crm.contacts.email", "crm.contacts.phone"},
        )
        # select j.value from crm.contacts c, lateral jsonb_each_text(to_jsonb(c)) j
        plan = node(
            "Nested Loop",
            ["j.value"],
            scan("contacts", ["c.*"], alias="c", parent="Outer"),
            {
                "Node Type": "Function Scan",
                "Parent Relationship": "Inner",
                "Alias": "j",
                "Function Call": "jsonb_each_text(to_jsonb(c.*))",
                "Output": ["j.key", "j.value"],
            },
        )
        self.assertEqual(blocked(plan), {"crm.contacts.email", "crm.contacts.phone"})

    def test_union_members_are_all_checked(self):
        # select name from crm.accounts union all select email from crm.contacts
        plan = node(
            "Append",
            None,
            scan("accounts", ["accounts.name"], parent="Member"),
            scan("contacts", ["contacts.email"], parent="Member"),
        )
        self.assertEqual(blocked(plan), {"crm.contacts.email"})

    def test_ctes_and_scalar_subqueries_are_checked(self):
        # with w as materialized (select email e from crm.contacts) select e from w
        cte = node(
            "CTE Scan",
            ["w.e"],
            scan("contacts", ["contacts.email"], parent="InitPlan", **{"Subplan Name": "CTE w"}),
            Alias="w",
        )
        self.assertEqual(blocked(cte), {"crm.contacts.email"})
        # select c.name, (select a.tax_id from crm.accounts a where ...) from crm.contacts c
        subplan = scan(
            "contacts",
            ["c.name", "(SubPlan 1)"],
            alias="c",
            Plans=[scan("accounts", ["a.tax_id"], alias="a", parent="SubPlan")],
        )
        self.assertEqual(blocked(subplan), {"crm.accounts.tax_id"})

    def test_scans_under_joins_and_aggregates_do_not_cause_false_positives(self):
        # select count(*) from crm.contacts: the scan prints its whole row.
        count = node("Aggregate", ["count(*)"], scan("contacts", ["contact_id", "email", "phone"], parent="Outer"))
        self.assertEqual(blocked(count), set())
        # select a.name from crm.contacts c join crm.accounts a using (account_id)
        join = node(
            "Hash Join",
            ["a.name"],
            scan("contacts", ["c.contact_id", "c.account_id", "c.email"], alias="c", parent="Outer"),
            node("Hash", ["a.name", "a.account_id"], scan("accounts", ["a.name", "a.account_id"], alias="a", parent="Outer"), parent="Inner"),
        )
        self.assertEqual(blocked(join), set())
        # A filter on a sensitive column does not return it.
        self.assertEqual(blocked(scan("contacts", ["name"], Filter="(email ~~ 'u1%'::text)")), set())

    def test_hidden_inputs_switch_to_checking_every_scan(self):
        # select v.x from crm.contacts c, lateral (values (c.email)) v(x)
        plan = node(
            "Nested Loop",
            ['"*VALUES*".column1'],
            scan("contacts", ["c.contact_id", "c.email"], alias="c", parent="Outer"),
            {"Node Type": "Values Scan", "Parent Relationship": "Inner", "Alias": "*VALUES*", "Output": ['"*VALUES*".column1']},
        )
        self.assertEqual(blocked(plan), {"crm.contacts.email"})

    def test_payroll_amounts_and_unknown_whole_rows(self):
        plan = scan("payroll", ["net_amt", "pay_date"], schema="hr")
        self.assertEqual(blocked(plan), {"hr.payroll.net_amt"})
        unknown = plan_source_columns([{"Plan": scan("mystery", ["to_jsonb(m.*)"], alias="m")}], COLUMNS)
        findings = classify_source_columns(unknown)
        self.assertEqual(findings[0]["category"], "unverified")

    def test_context_policy_applies_to_source_columns(self):
        findings = classify_source_columns({("crm.contacts", "name")}, {"name": "restricted"})
        self.assertEqual(findings[0]["detection"], "approved_context_policy")


if __name__ == "__main__":
    unittest.main()
