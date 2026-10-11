from __future__ import annotations

import heapq
import json
import math
import re
from collections import Counter, OrderedDict
from copy import deepcopy
from itertools import combinations
from typing import Any

from .graph import GraphEngine
from .models import GraphNode, GraphSnapshot

CONTEXT_CACHE_SIZE = 256
RELATION_KINDS = {"table", "view", "materialized_view"}
STOP_WORDS = {
    "a", "all", "along", "an", "and", "are", "average", "been", "change", "changes",
    "done", "find", "for", "from", "get", "had", "has", "have", "her", "his", "in",
    "is", "last", "list", "made", "make", "me", "most", "my", "of", "recently",
    "show", "the", "their", "they", "to", "was", "were", "what", "which", "who",
    "with", "year",
    # Conversational filler in vague questions; never schema vocabulary worth matching.
    "us", "our", "ours", "we", "how", "why", "do", "does", "did", "any", "anything", "anybody",
    "there", "this", "that", "these", "those", "so", "far", "right", "now", "just", "actually",
    "even", "keep", "kept", "got", "going", "be", "being", "on", "at", "by", "or", "it", "its",
    "stuff", "thing", "things", "lot", "lots", "really", "kind", "anyone", "someone", "anywhere",
}


# Everyday business words mapped to the words schemas tend to use. Generic on purpose:
# it must help any business database, so nothing here names a specific table.
CONCEPTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("owe", "owed", "owes", "owing", "debt", "debts", "outstanding", "unpaid", "receivable", "receivables"): (
        "invoice", "balance", "due", "outstanding", "unpaid", "receivable", "payment",
    ),
    ("paid", "pay", "pays", "payment", "payments", "collect", "collected", "collections"): (
        "payment", "paid", "invoice", "amount",
    ),
    ("churn", "churned", "churning", "leaving", "left", "quit", "quitting", "lost", "cancel", "cancelled",
     "canceled", "cancellation", "cancellations", "unsubscribe", "unsubscribed"): (
        "churn", "cancel", "cancelled", "canceled", "cancellation", "subscription", "ended", "reason",
    ),
    ("complain", "complaining", "complaint", "complaints", "unhappy", "angry", "upset", "issue", "issues",
     "problem", "problems", "escalation", "escalations"): (
        "ticket", "complaint", "issue", "case", "support", "escalation", "priority",
    ),
    ("satisfaction", "satisfied", "nps", "rating", "ratings", "review", "reviews", "feedback", "happy"): (
        "rating", "review", "satisfaction", "csat", "nps", "feedback", "score",
    ),
    ("seller", "sellers", "salesperson", "salespeople", "salesman", "rep", "reps", "closer", "closers"): (
        "rep", "salesperson", "employee", "owner", "order", "opportunity",
    ),
    ("customer", "customers", "client", "clients", "buyer", "buyers"): ("customer", "client", "account", "buyer"),
    ("revenue", "sales", "income", "earn", "earned", "earnings", "turnover", "sold", "selling"): (
        "revenue", "amount", "total", "order", "invoice", "price",
    ),
    ("spend", "spending", "spent", "cost", "costs", "expense", "expenses", "bleeding", "burn", "burning"): (
        "expense", "cost", "amount", "spend", "purchase", "debit", "budget",
    ),
    ("profit", "profitable", "margin", "margins"): ("margin", "cost", "price", "revenue"),
    ("stock", "inventory", "shortage", "shortages", "reorder", "restock", "stockout"): (
        "stock", "inventory", "hand", "quantity", "qty", "reorder", "warehouse", "available", "reserved",
    ),
    ("ship", "shipping", "shipped", "shipment", "shipments", "deliver", "delivery", "deliveries", "delivered"): (
        "shipment", "shipping", "delivery", "delivered", "shipped", "carrier", "tracking",
    ),
    ("late", "delay", "delayed", "delays", "overdue", "slow", "behind", "missed"): (
        "due", "expected", "late", "delay", "overdue", "sla", "breach",
    ),
    ("employee", "employees", "staff", "team", "teams", "headcount", "hire", "hires", "hired",
     "hiring", "fired", "workforce"): ("employee", "staff", "headcount", "hire", "hired", "department", "team"),
    ("salary", "salaries", "payroll", "compensation", "wage", "wages", "paycheck"): (
        "salary", "payroll", "compensation", "pay", "net", "gross",
    ),
    ("suspicious", "hack", "hacked", "hacker", "breach", "attack", "attacks", "fraud", "security", "intrusion",
     "bruteforce", "fishy", "shady", "sketchy", "unusual", "anomaly", "anomalies"): (
        "login", "auth", "failed", "attempt", "event", "audit", "ip", "blocked",
    ),
    ("login", "logins", "signin", "logon", "password", "passwords"): ("login", "auth", "failed", "attempt", "session"),
    ("campaign", "campaigns", "marketing", "ads", "advert", "advertising", "promo", "promotion", "promotions",
     "newsletter", "funnel", "conversion", "conversions", "lead", "leads"): (
        "campaign", "marketing", "lead", "conversion", "email", "promo", "click", "open",
    ),
    ("website", "site", "traffic", "visitor", "visitors", "visit", "visits", "pageviews", "browsing", "bounce"): (
        "session", "page", "view", "visit", "web", "traffic",
    ),
    ("product", "products", "item", "items", "sku", "skus", "goods", "merchandise"): ("product", "item", "sku"),
    ("return", "returns", "returned", "refund", "refunds", "refunded"): ("return", "refund", "reason"),
    ("deal", "deals", "pipeline", "quota", "quotas", "forecast", "prospect", "prospects"): (
        "opportunity", "pipeline", "deal", "stage", "forecast", "quota",
    ),
    ("usage", "adoption", "feature", "features", "engagement", "engaged", "active"): (
        "usage", "feature", "active", "event", "session",
    ),
    ("subscription", "subscriptions", "plan", "plans", "mrr", "arr", "recurring", "renew", "renewal", "renewals"): (
        "subscription", "plan", "mrr", "renewal", "recurring",
    ),
    ("supplier", "suppliers", "vendor", "vendors", "procurement", "purchasing", "sourcing"): (
        "supplier", "vendor", "purchase", "po",
    ),
    ("error", "errors", "fail", "failing", "failed", "failure", "failures", "broken", "outage", "outages",
     "down", "bug", "bugs", "crash", "crashes"): ("error", "failed", "status", "incident", "log", "request"),
    ("audit", "change", "changed", "edited", "modified", "tampered", "history"): ("audit", "change", "history", "log"),
    ("export", "exported", "exporting", "download", "downloaded", "extract", "extracted", "pulling"): (
        "export", "download", "audit", "action",
    ),
    ("delete", "deleted", "deleting", "deletion", "removed", "wiped"): ("delete", "deleted", "audit", "action"),
    ("quiet", "silent", "inactive", "contacted", "talked", "touch", "reached", "interaction", "interactions"): (
        "activity", "activities", "contact", "interaction", "last",
    ),
    ("promise", "promises", "promised", "commitment", "commitments", "response", "respond", "responded"): (
        "sla", "breach", "response", "target",
    ),
    ("anymore", "discontinued", "dead", "obsolete", "retired", "sunset"): (
        "discontinued", "active", "status", "inactive",
    ),
    ("using", "use", "uses", "used"): ("usage", "feature", "event"),
    ("longest", "slowest", "fastest", "quickest", "duration", "turnaround"): (
        "delivered", "shipped", "resolved", "closed", "duration",
    ),
    ("region", "regions", "country", "countries", "territory", "territories", "market", "markets", "geo"): (
        "region", "country", "territory",
    ),
}
CONCEPT_PHRASES: dict[str, tuple[str, ...]] = {
    "log in": ("login", "auth", "attempt", "failed"),
    "logged in": ("login", "auth", "attempt"),
    "sign in": ("login", "auth", "attempt", "failed"),
    "sending back": ("return", "refund", "reason"),
    "send back": ("return", "refund", "reason"),
    "sent back": ("return", "refund", "reason"),
    "flying off": ("order", "item", "quantity", "qty", "product"),
    "best seller": ("order", "item", "quantity", "qty", "product"),
    "best selling": ("order", "item", "quantity", "qty", "product"),
    "running low": ("stock", "inventory", "hand", "reorder", "quantity", "available"),
    "run out": ("stock", "inventory", "hand", "reorder", "quantity", "available"),
    "out of stock": ("stock", "inventory", "hand", "reorder", "quantity", "available"),
    "low on": ("stock", "inventory", "hand", "reorder", "quantity", "available"),
    "sign up": ("signup", "registration", "created"),
    "signed up": ("signup", "registration", "created"),
}
# Words that ask for a ranking, a total or a trend. Such questions need the table where
# the transactions live (amounts, dates, many foreign keys), which they rarely name.
AGGREGATE_INTENT = {
    "best", "top", "biggest", "largest", "most", "highest", "worst", "lowest", "least", "fewest",
    "total", "totals", "revenue", "trend", "trending", "trends", "growth", "growing", "grow", "declining",
    "monthly", "weekly", "daily", "quarterly", "yearly", "average", "compare", "valuable", "loyal",
    "spend", "spent", "much", "many", "volume",
}
MONEY_OR_COUNT_TERMS = {
    "amt", "amount", "total", "price", "revenue", "cost", "value", "balance", "fee", "subtotal",
    "net", "gross", "qty", "quantity", "units",
}
NUMERIC_TYPES = {"bigint", "decimal", "double precision", "integer", "numeric", "real", "smallint", "money"}
STATUS_TERMS = {"status", "state", "stage", "phase", "outcome", "result"}
STATUS_FLAGS = {"active", "archived", "deleted", "enabled", "success", "succeeded", "failed", "escalated"}
# Short type names shown next to each column ("total_amt numeric").
TYPE_ABBREVIATIONS = {
    "ARRAY": "array",
    "USER-DEFINED": "enum",
    "boolean": "bool",
    "character": "char",
    "character varying": "varchar",
    "double precision": "float8",
    "integer": "int",
    "real": "float4",
    "time with time zone": "timetz",
    "time without time zone": "time",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
}
# Tables this narrow are listed in full; wider ones get matches, keys and essentials.
SMALL_TABLE_COLUMNS = 10
MAX_ESSENTIAL_COLUMNS = 8
MAX_ALLOWED_VALUES = 12
CONCEPT_WEIGHT = 0.7
# Words whose stem collides with a concept trigger but mean something else:
# "invoices issued in Q3" are not support issues.
NOT_CONCEPTS = {"issued", "issuing"}
# Multi-hop questions name both ends of a foreign-key chain ("invoices by region"); at most
# this many named relations are joined, through at most this many connecting tables.
MAX_NAMED_ANCHORS = 4
MAX_CHAIN_CONNECTORS = 3
# A business word the schema already uses is understood; expand it only lightly.
KNOWN_CONCEPT_WEIGHT = 0.35
# "Who ..." asks about a party: a customer, employee, account, supplier.
PARTY_TERMS = {
    "customer", "customers", "client", "clients", "account", "accounts", "employee", "employees",
    "user", "users", "member", "members", "person", "people", "contact", "contacts", "supplier",
    "suppliers", "vendor", "vendors", "agent", "agents", "rep", "reps", "staff", "buyer", "buyers",
}


def _stem(word: str) -> str:
    """Light suffix stripping so inflections match ("leaving"/"leave", "handled"/"handle")."""
    for suffix in ("ies", "ing", "ers", "ed", "er", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)] + ("y" if suffix == "ies" else "")
            break
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


CONCEPT_INDEX: dict[str, tuple[str, ...]] = {}
for _triggers, _terms in CONCEPTS.items():
    for _trigger in _triggers:
        # Keyed by the word and its stem, so "changing" finds the "changed" concept.
        for _key in {_trigger, _stem(_trigger)}:
            CONCEPT_INDEX[_key] = tuple(dict.fromkeys(CONCEPT_INDEX.get(_key, ()) + _terms))


def _tokens(value: Any) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if token not in STOP_WORDS
    ]


def _name_tokens(value: Any) -> tuple[list[str], dict[str, list[tuple[str, ...]]]]:
    tokens = _tokens(value)
    expansions: dict[str, list[tuple[str, ...]]] = {}
    for size in range(2, min(4, len(tokens) + 1)):
        for parts in combinations(tokens[:5], size):
            acronym = "".join(part[0] for part in parts)
            if len(acronym) >= 2:
                expansions.setdefault(acronym, []).append(parts)
    return tokens, expansions


def _trigrams(value: str) -> set[str]:
    padded = f"  {value} "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


def _similarity(left: str, right: str) -> float:
    left_parts, right_parts = _trigrams(left), _trigrams(right)
    return 2 * len(left_parts & right_parts) / (len(left_parts) + len(right_parts))


def _column_type(column: GraphNode) -> str:
    """Compact type, plus the allowed values of an enum or CHECK list: "status text: open|paid"."""
    data_type = str(column.metadata.get("data_type") or "")
    label = TYPE_ABBREVIATIONS.get(data_type, data_type)
    values = [str(value)[:40] for value in column.metadata.get("allowed_values") or []]
    if values:
        shown = values[:MAX_ALLOWED_VALUES]
        label += ": " + "|".join(shown) + ("|..." if len(values) > len(shown) else "")
    return label


class RetrievalIndex:
    """Small in-memory schema index; never indexes application rows."""

    def __init__(self, snapshot: GraphSnapshot) -> None:
        self.snapshot = snapshot
        # Per-instance so a discarded index (and its snapshot) can be collected.
        self._cache: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()
        self.nodes = {node.id: node for node in snapshot.nodes}
        self.relations = {
            node.id: node for node in snapshot.nodes if node.kind in RELATION_KINDS
        }
        self.columns: dict[str, list[GraphNode]] = {key: [] for key in self.relations}
        for node in snapshot.nodes:
            if node.kind == "column" and node.parent_id in self.columns:
                self.columns[node.parent_id].append(node)

        self.key_roles: dict[str, dict[str, str]] = {
            key: {} for key in self.relations
        }
        for node in snapshot.nodes:
            if node.kind != "constraint" or node.parent_id not in self.key_roles:
                continue
            role = str(node.metadata.get("constraint_type") or "key").lower()
            for column in node.metadata.get("columns", []):
                self.key_roles[node.parent_id][str(column)] = role

        self.column_types: dict[str, dict[str, str]] = {
            relation_id: {str(column.name): _column_type(column) for column in columns}
            for relation_id, columns in self.columns.items()
        }
        self.term_frequencies: dict[str, Counter[str]] = {}
        self.column_terms: dict[str, dict[str, set[str]]] = {}
        self.acronym_phrases: dict[str, Counter[tuple[str, ...]]] = {}
        document_frequency: Counter[str] = Counter()
        lengths = []
        for relation_id, relation in self.relations.items():
            frequencies: Counter[str] = Counter()
            relation_terms, expansions = _name_tokens(relation.label)
            frequencies.update({term: 4 for term in relation_terms})
            for acronym, phrases in expansions.items():
                self.acronym_phrases.setdefault(acronym, Counter()).update(phrases)
            per_column = {}
            for column in self.columns[relation_id]:
                name_terms, expansions = _name_tokens(column.name)
                terms = set(name_terms)
                for acronym, phrases in expansions.items():
                    self.acronym_phrases.setdefault(acronym, Counter()).update(phrases)
                per_column[str(column.name)] = terms
                frequencies.update({term: 2 for term in terms})
                frequencies.update(_tokens(column.metadata.get("comment")))
            frequencies.update({term: 2 for term in _tokens(relation.metadata.get("comment"))})
            context = relation.metadata.get("context", {})
            frequencies.update(_tokens(context.get("description")))
            frequencies.update(_tokens(context.get("owner")))
            self.term_frequencies[relation_id] = frequencies
            self.column_terms[relation_id] = per_column
            document_frequency.update(frequencies.keys())
            lengths.append(sum(frequencies.values()))

        self.document_frequency = document_frequency
        self.vocabulary = set(document_frequency)
        self.stems: dict[str, set[str]] = {}
        for token in self.vocabulary:
            self.stems.setdefault(_stem(token), set()).add(token)
        self.average_length = sum(lengths) / max(1, len(lengths))

        # Foreign-key neighbours and a "transaction table" score, used when a question asks
        # for a ranking, total or trend without naming the table that holds the facts.
        self.neighbours: dict[str, set[str]] = {key: set() for key in self.relations}
        self.references: dict[str, set[str]] = {key: set() for key in self.relations}
        self.referenced_by: dict[str, set[str]] = {key: set() for key in self.relations}
        outgoing: Counter[str] = Counter()
        for edge in snapshot.edges:
            if edge.kind == "foreign_key" and edge.source in self.relations and edge.target in self.relations:
                self.neighbours[edge.source].add(edge.target)
                self.neighbours[edge.target].add(edge.source)
                self.references[edge.source].add(edge.target)
                self.referenced_by[edge.target].add(edge.source)
                outgoing[edge.source] += 1
        row_counts = sorted(int(rel.metadata.get("row_estimate") or 0) for rel in self.relations.values())
        median_rows = row_counts[len(row_counts) // 2] if row_counts else 0
        self.fact_scores: dict[str, float] = {}
        for relation_id, relation in self.relations.items():
            money = dated = False
            for column in self.columns[relation_id]:
                data_type = str(column.metadata.get("data_type") or "")
                if data_type in NUMERIC_TYPES and self.column_terms[relation_id].get(str(column.name), set()) & MONEY_OR_COUNT_TERMS:
                    money = True
                if data_type.startswith(("date", "timestamp")):
                    dated = True
            rows = int(relation.metadata.get("row_estimate") or 0)
            self.fact_scores[relation_id] = (
                float(money) + float(dated) + min(outgoing[relation_id], 3) / 3 + (0.5 if rows > median_rows else 0.0)
            )

    def _expanded_terms(
        self,
        question: str,
    ) -> tuple[dict[str, float], dict[str, str], dict[str, str]]:
        """Question terms with weights: exact words count fully, guesses count less."""
        # Two-letter words are only acronyms when written that way ("US", not "us"), and a
        # word written in capitals is kept even if it is normally conversational filler.
        upper = {token.lower() for token in re.findall(r"\b[A-Z]{2,}\b", question)}
        original = list(dict.fromkeys([*_tokens(question), *sorted(upper)]))
        original_set = set(original)
        weights: dict[str, float] = {}
        fuzzy: dict[str, str] = {}
        acronyms: dict[str, str] = {}

        def add(term: str, weight: float) -> None:
            if weight > weights.get(term, 0.0):
                weights[term] = weight

        for term in original:
            in_vocabulary = term in self.vocabulary
            if in_vocabulary:
                add(term, 1.0)
            has_digit = any(char.isdigit() for char in term)
            # Only treat a term as an acronym when the schema does not already use it as
            # a word, otherwise ordinary words expand into unrelated column phrases.
            phrases = None
            if not in_vocabulary and not has_digit and (len(term) >= 3 or term in upper):
                phrases = self.acronym_phrases.get(term)
            if phrases:
                phrase, _ = max(
                    phrases.items(),
                    key=lambda item: (
                        sum(part in original_set for part in item[0]),
                        item[1],
                        -len(item[0]),
                        item[0],
                    ),
                )
                for part in phrase:
                    add(part, 0.9)
                acronyms[term] = " ".join(phrase)
                continue
            if has_digit:
                # Numbers (years, ids) are never typos of other numbers.
                continue
            # Singular/plural are the same word in both directions, never a typo.
            variants = [] if len(term) < 3 else sorted(
                token
                for token in self.vocabulary
                if token in {f"{term}s", f"{term}es"}
                or (term.endswith("s") and token == term[:-1])
                or (term.endswith("es") and token == term[:-2])
            )
            for token in variants[:3]:
                add(token, 1.0)
            if in_vocabulary or variants:
                continue
            stem_matches = sorted(self.stems.get(_stem(term), set())) if len(term) >= 4 else []
            for token in stem_matches[:3]:
                add(token, 0.8)
            if stem_matches:
                fuzzy[term] = stem_matches[0]
                continue
            abbreviations = sorted(
                token
                for token in self.vocabulary
                if len(token) >= 3 and term.startswith(token)
            )
            for token in abbreviations[:3]:
                add(token, 0.5)
            if abbreviations:
                fuzzy[term] = abbreviations[0]
            if len(term) < 5:
                # Short tokens produce noise ("all" scoring against "allow"), and the scan
                # below costs a full vocabulary pass, so skip them.
                continue
            # ponytail: vocabulary scan is fine for schema-sized indexes; add trigram postings
            # only if a benchmark shows more than 10k relations.
            matches = heapq.nlargest(
                2,
                ((_similarity(term, token), token) for token in self.vocabulary),
            )
            for score, token in matches:
                # Typos only: near-identical spelling, so "generated" never becomes "general".
                if score >= 0.75 and abs(len(token) - len(term)) <= 2:
                    add(token, 0.6)
                    fuzzy[term] = token

        # Business vocabulary: everyday words to the words schemas use.
        question_lower = question.lower()
        concept_terms: dict[str, float] = {}
        for term in original:
            if term in NOT_CONCEPTS:
                continue
            weight = KNOWN_CONCEPT_WEIGHT if term in self.vocabulary else CONCEPT_WEIGHT
            for concept in CONCEPT_INDEX.get(term, CONCEPT_INDEX.get(_stem(term), ())):
                concept_terms[concept] = max(concept_terms.get(concept, 0.0), weight)
        for phrase, terms in CONCEPT_PHRASES.items():
            if phrase in question_lower:
                for concept in terms:
                    concept_terms[concept] = max(concept_terms.get(concept, 0.0), CONCEPT_WEIGHT)
        for term, weight in concept_terms.items():
            for token in [term, *(self.stems.get(_stem(term), set()) if len(term) >= 4 else ())]:
                if token in self.vocabulary:
                    add(token, weight)
        return weights, fuzzy, acronyms

    def _named_chain(
        self,
        question: str,
        terminal_ids: list[str],
        scores: dict[str, float],
    ) -> tuple[list[str], list[str]] | None:
        """The relations a question names in full, and the foreign-key chain joining them.

        "Total invoiced amount by sales region" names invoices and regions; the answer also
        needs every table between them (customers, accounts), which match no word and
        otherwise rank below speculative matches. Returns None unless at least two named
        relations, the strongest match among them, join through a short chain.
        """
        if not terminal_ids:
            return None
        words = {_stem(token) for token in _tokens(question)}
        named = []
        for relation_id in sorted(scores, key=lambda item: -scores[item]):
            name_terms = [_stem(token) for token in _tokens(self.relations[relation_id].name)]
            if name_terms and all(term in words for term in name_terms):
                named.append(relation_id)
        leader = terminal_ids[0]
        anchors = [leader, *(item for item in named if item != leader)][:MAX_NAMED_ANCHORS]
        if len(anchors) < 2:
            return None
        paths = []
        for index, source in enumerate(anchors):
            for target in anchors[index + 1 :]:
                path = self._chain_path(source, target, scores)
                if path:
                    paths.append((len(path), source, target, path))
        parent = {anchor: anchor for anchor in anchors}

        def find(item: str) -> str:
            while parent[item] != item:
                item = parent[item]
            return item

        connectors: list[str] = []
        for _, source, target, path in sorted(paths):
            if find(source) != find(target):
                parent[find(source)] = find(target)
                connectors += [node for node in path[1:-1] if node not in anchors and node not in connectors]
        connected = len({find(anchor) for anchor in anchors}) == 1
        # Directly joined names need no help; long chains are more likely wrong than right.
        if not connected or not 1 <= len(connectors) <= MAX_CHAIN_CONNECTORS:
            return None
        return anchors, connectors

    def _chain_path(
        self,
        source: str,
        target: str,
        scores: dict[str, float],
        max_hops: int = 4,
    ) -> list[str] | None:
        """Shortest foreign-key path that never passes through a shared lookup table.

        `invoices -> currencies <- regions` joins two tables only because both reference the
        same lookup, which answers nothing; real chains follow references in one direction
        or fork from a table that references both sides (invoice_lines -> invoices and
        invoice_lines -> order_items). So a step that arrived along a reference may not
        leave against one. Among equally short paths, the one through tables that matched
        the question better wins (customers over warehouses for "sales region").
        """
        start = (source, False)
        previous: dict[tuple[str, bool], tuple[str, bool] | None] = {start: None}
        gathered = {start: 0.0}
        frontier = [start]
        for _ in range(max_hops):
            following = []
            arrivals = []
            for state in sorted(frontier, key=lambda item: (-gathered[item], item)):
                node, arrived_forward = state
                steps = [(item, True) for item in sorted(self.references[node])]
                if not arrived_forward:
                    steps += [(item, False) for item in sorted(self.referenced_by[node])]
                for step in steps:
                    if step[0] == node or step in previous:
                        continue
                    previous[step] = state
                    gathered[step] = gathered[state] + scores.get(step[0], 0.0)
                    (arrivals if step[0] == target else following).append(step)
            if arrivals:
                cursor: tuple[str, bool] | None = max(arrivals, key=lambda item: gathered[item])
                path = []
                while cursor is not None:
                    path.append(cursor[0])
                    cursor = previous[cursor]
                return path[::-1]
            frontier = following
        return None

    def _essential_columns(self, relation_id: str, column_names: set[str]) -> list[str]:
        """Status, date and amount columns, the ones questions use without naming them."""
        status, dates, amounts = [], [], []
        for column in self.columns[relation_id]:
            name = str(column.name)
            if name.endswith("_time_zone") and name.removesuffix("_time_zone") in column_names:
                continue
            data_type = str(column.metadata.get("data_type") or "")
            terms = self.column_terms[relation_id].get(name, set())
            flag = data_type == "boolean" and (name.startswith(("is_", "has_")) or terms & STATUS_FLAGS)
            if terms & STATUS_TERMS or data_type == "USER-DEFINED" or flag:
                status.append(name)
            elif data_type.startswith(("date", "timestamp")):
                dates.append(name)
            elif data_type in NUMERIC_TYPES and terms & MONEY_OR_COUNT_TERMS:
                amounts.append(name)
        return status + dates + amounts

    def _foreign_key_neighbours(self, relation_id: str, limit: int) -> list[str]:
        """Relations reachable by one declared foreign key, targets before sources."""
        if limit <= 0:
            return []
        targets: list[str] = []
        sources: list[str] = []
        for edge in self.snapshot.edges:
            if edge.kind != "foreign_key":
                continue
            if edge.source == relation_id and edge.target in self.relations:
                targets.append(edge.target)
            elif edge.target == relation_id and edge.source in self.relations:
                sources.append(edge.source)
        ordered = list(dict.fromkeys(targets + sources))
        return [item for item in ordered if item != relation_id][:limit]

    def _rank(
        self,
        question: str,
        limit: int,
    ) -> tuple[list[tuple[float, str]], list[str], dict[str, str], dict[str, str]]:
        weights, fuzzy, acronyms = self._expanded_terms(question)
        terms = list(weights)
        ranked = []
        question_lower = question.lower()
        historical = any(
            phrase in question_lower
            for phrase in ("ago", "histor", "last ", "past ", "previous ")
        )
        relation_count = max(1, len(self.relations))
        for relation_id, frequencies in self.term_frequencies.items():
            length = sum(frequencies.values())
            score = 0.0
            for term in terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                documents = self.document_frequency[term]
                inverse = math.log(1 + (relation_count - documents + 0.5) / (documents + 0.5))
                denominator = frequency + 1.2 * (
                    0.25 + 0.75 * length / max(1, self.average_length)
                )
                score += weights[term] * inverse * frequency * 2.2 / denominator
            relation = self.relations[relation_id]
            relation_name = str(relation.name).lower()
            relation_name_terms = set(_tokens(relation_name))
            score += 0.75 * sum(weights[term] for term in relation_name_terms & weights.keys())
            if relation_name in question_lower:
                score += 5
            row_estimate = int(relation.metadata.get("row_estimate") or 0)
            if row_estimate > 0:
                score += min(0.25, math.log10(row_estimate + 1) * 0.05)
            if relation.metadata.get("context", {}).get("source_of_truth"):
                score += 10
            if historical:
                if relation_name_terms & {"actual", "history", "log"}:
                    score += 3
                if relation_name_terms & {"forecast", "plan", "schedule"}:
                    score -= 2
            if relation_name_terms & {"archive", "backup", "cache", "deleted", "temp"}:
                score -= 6
            if "notification" in relation_name_terms and "notification" not in question_lower:
                score -= 2
            if "report" in relation_name_terms and "report" not in question_lower:
                score -= 4
            column_count = len(self.columns[relation_id])
            if column_count and len(self.key_roles[relation_id]) / column_count >= 0.75:
                score -= 8
            identifier_columns = sum(
                str(column.name).endswith(("_id", "_idx"))
                for column in self.columns[relation_id]
            )
            if column_count and identifier_columns / column_count >= 0.75:
                score -= 8
            if relation.kind == "table":
                score += 0.1
            if score:
                ranked.append((score, relation_id))
        # "top-level category" is a hierarchy, not a ranking.
        question_words = set(re.findall(r"[a-z]+", question_lower.replace("top-level", "")))
        aggregate = bool(question_words & AGGREGATE_INTENT)
        asks_who = bool(question_words & {"who", "whom", "whose"})
        if ranked and (aggregate or asks_who):
            scores = {relation_id: score for score, relation_id in ranked}
            leaders = {relation_id: score for score, relation_id in heapq.nlargest(4, ranked)}
            nearby = set().union(*(self.neighbours[relation_id] for relation_id in leaders))
            for relation_id in set(scores) | nearby:
                relation = self.relations[relation_id]
                # How strongly the matches this table joins to were matched.
                support = sum(
                    score for leader, score in leaders.items()
                    if leader != relation_id and relation_id in self.neighbours[leader]
                )
                bonus = 0.0
                if aggregate:
                    # "Who are our best customers?" names customers but needs the orders
                    # table: boost transaction tables among the matches and their neighbours.
                    fact = self.fact_scores[relation_id]
                    bonus += 1.2 * fact + 0.3 * support * min(1.0, fact / 3)
                    if relation.kind != "table":
                        bonus *= 0.5
                if asks_who and set(_tokens(relation.name)) & PARTY_TERMS:
                    # The party joined to the strongest matches is usually who is meant.
                    bonus += 0.5 * support
                if relation_id not in scores:
                    if not bonus:
                        continue
                    bonus *= 0.9
                scores[relation_id] = scores.get(relation_id, 0.0) + bonus
            ranked = [(score, relation_id) for relation_id, score in scores.items()]
        return heapq.nlargest(limit, ranked), terms, fuzzy, acronyms

    def context(
        self,
        question: str,
        max_relations: int = 6,
        max_bytes: int = 6_144,
    ) -> dict[str, Any]:
        cache_key = (question, max_relations, max_bytes)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return deepcopy(cached)
        max_relations = max(1, min(max_relations, 12))
        max_bytes = max(2_048, min(max_bytes, 32_768))
        ranked, terms, fuzzy, acronyms = self._rank(question, max_relations * 4)
        scores = {relation_id: score for score, relation_id in ranked}
        if ranked:
            confidence_floor = max(1.5, ranked[0][0] * 0.30)
            candidates = [item for item in ranked if item[0] >= confidence_floor]
            diverse = []
            signature_counts: Counter[frozenset[str]] = Counter()
            while candidates and len(diverse) < max_relations:
                def adjusted(item: tuple[float, str]) -> float:
                    signature = frozenset(
                        term
                        for term in terms
                        if self.term_frequencies[item[1]].get(term)
                    )
                    return item[0] - 2.5 * signature_counts[signature]

                selected = max(candidates, key=adjusted)
                candidates.remove(selected)
                diverse.append(selected)
                signature_counts[
                    frozenset(
                        term
                        for term in terms
                        if self.term_frequencies[selected[1]].get(term)
                    )
                ] += 1
            ranked = diverse
        terminal_ids = [relation_id for _, relation_id in ranked]
        chain = self._named_chain(question, terminal_ids, scores)
        if chain:
            anchors, chain_connectors = chain
            others = [item for item in terminal_ids if item not in anchors and item not in chain_connectors]
            # The named relations and the tables joining them come first; speculative
            # matches fill the remaining ranked slots.
            terminal_ids = anchors + chain_connectors + others[: max(0, max_relations - len(anchors))]
        # Prefer real foreign-key joins: a view's dependency links are not join paths and
        # otherwise act as shortcuts that skip the tables a query must actually join.
        subgraph = GraphEngine.join_subgraph(self.snapshot, terminal_ids, edge_kinds=frozenset({"foreign_key"}))
        if not subgraph["connected"]:
            subgraph = GraphEngine.join_subgraph(self.snapshot, terminal_ids)
        connectors = [node for node in subgraph["nodes"] if node not in terminal_ids]
        # Tables that join the strongest matches are required for the answer; list them
        # before lower-ranked, speculative matches.
        lead = len(chain[0]) + len(chain[1]) if chain else 3
        relation_ids = terminal_ids[:lead] + connectors + terminal_ids[lead:]
        if len(terminal_ids) == 1:
            # A single confident match still needs its lookup tables, and join_subgraph
            # returns early for one terminal, so add direct foreign-key neighbours.
            relation_ids += self._foreign_key_neighbours(
                terminal_ids[0], max(0, max_relations - 1)
            )
        relations = []
        for relation_id in relation_ids:
            relation = self.relations.get(relation_id)
            if relation is None:
                continue
            matching = []
            keys = []
            column_names = {str(column.name) for column in self.columns[relation_id]}
            for column in self.columns[relation_id]:
                column_name = str(column.name)
                if (
                    column_name.endswith("_time_zone")
                    and column_name.removesuffix("_time_zone") in column_names
                ):
                    continue
                overlap = self.column_terms[relation_id][column_name] & set(terms)
                role = self.key_roles[relation_id].get(column_name)
                if overlap:
                    data_type = str(column.metadata.get("data_type") or "")
                    metric = data_type in {
                        "bigint",
                        "decimal",
                        "double precision",
                        "integer",
                        "numeric",
                        "real",
                        "smallint",
                    }
                    matching.append(
                        (-len(overlap), -int(metric), tuple(sorted(overlap)), column_name)
                    )
                elif role:
                    keys.append(column_name)
            matching.sort()
            signature_counts: Counter[tuple[str, ...]] = Counter()
            selected = []
            for _, _, signature, column_name in matching:
                if signature_counts[signature] >= 2:
                    continue
                selected.append(column_name)
                signature_counts[signature] += 1
                if len(selected) >= 10:
                    break
            keys.sort(key=lambda name: (0 if name.endswith("_id") else 1, name))
            for name in keys:
                if name not in selected and len(selected) < 12:
                    selected.append(name)
            if relation_id in terminal_ids:
                # Most wrong answers came from guessed date, amount and status columns,
                # so every ranked relation lists them whatever the question's wording.
                essentials = self._essential_columns(relation_id, column_names)
                selected += [name for name in essentials if name not in selected][:MAX_ESSENTIAL_COLUMNS]
                if len(self.columns[relation_id]) <= SMALL_TABLE_COLUMNS:
                    selected += [
                        str(column.name)
                        for column in self.columns[relation_id]
                        if str(column.name) not in selected
                        and str(column.name).removesuffix("_time_zone") not in selected
                    ]

            if relation_id not in terminal_ids:
                for column in self.columns[relation_id]:
                    name = str(column.name)
                    if name in selected:
                        continue
                    if name == "name" or name.endswith("_name") or name in {
                        "first_name",
                        "last_name",
                    }:
                        selected.append(name)
                    if len(selected) >= 4:
                        break
            types = self.column_types[relation_id]
            relations.append(
                {
                    "id": relation_id,
                    "columns": [f"{name} {types[name]}" if types.get(name) else name for name in selected],
                }
            )

        joins = []
        for edge in subgraph["edges"]:
            metadata = edge.get("metadata", {})
            joins.append(
                [
                    edge["source"],
                    metadata.get("columns", []),
                    edge["target"],
                    metadata.get("foreign_columns", []),
                    edge["kind"],
                ]
            )
        response = {
            "interpreted": [
                *(f"{key}={value}" for key, value in acronyms.items()),
                *(f"{key}~{value}" for key, value in fuzzy.items()),
            ],
            "relations": relations,
            "joins": joins,
            "connected": subgraph["connected"],
        }
        self._fit(response, max_bytes)
        # Store a copy: callers (the delivery window) mutate the response they receive.
        self._cache[cache_key] = deepcopy(response)
        while len(self._cache) > CONTEXT_CACHE_SIZE:
            self._cache.popitem(last=False)
        return response

    @staticmethod
    def _fit(response: dict[str, Any], max_bytes: int) -> None:
        def size() -> int:
            return len(json.dumps(response, separators=(",", ":"), default=str).encode())

        while size() > max_bytes - 32:
            relation = next(
                (item for item in reversed(response["relations"]) if item["columns"]),
                None,
            )
            if relation:
                relation["columns"].pop()
                response["truncated"] = True
                continue
            if len(response["relations"]) <= 1:
                raise ValueError("The minimum task context exceeds max_bytes.")
            response["relations"].pop()
            visible = {item["id"] for item in response["relations"]}
            response["joins"] = [
                join for join in response["joins"] if join[0] in visible and join[2] in visible
            ]
            response["omitted_relations"] = response.get("omitted_relations", 0) + 1
            response["truncated"] = True
        response["bytes"] = size()
