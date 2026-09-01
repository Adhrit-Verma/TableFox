from __future__ import annotations

from collections import Counter
from functools import lru_cache
import heapq
from itertools import combinations
import json
import math
import re
from typing import Any

from .graph import GraphEngine
from .models import GraphNode, GraphSnapshot


RELATION_KINDS = {"table", "view", "materialized_view"}
STOP_WORDS = {
    "a", "an", "and", "for", "from", "get", "his", "her", "in", "last",
    "me", "of", "show", "the", "their", "to", "with", "year",
}


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


class RetrievalIndex:
    """Small in-memory schema index; never indexes application rows."""

    def __init__(self, snapshot: GraphSnapshot) -> None:
        self.snapshot = snapshot
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
        self.average_length = sum(lengths) / max(1, len(lengths))

    def _expanded_terms(
        self,
        question: str,
    ) -> tuple[list[str], dict[str, str], dict[str, str]]:
        original = list(dict.fromkeys(_tokens(question)))
        original_set = set(original)
        expanded = []
        fuzzy: dict[str, str] = {}
        acronyms: dict[str, str] = {}
        for term in original:
            if term in self.vocabulary:
                expanded.append(term)
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
                expanded.extend(phrase)
                acronyms[term] = " ".join(phrase)
                continue
            prefixes = sorted(
                token
                for token in self.vocabulary
                if token in {f"{term}s", f"{term}es"}
            )
            if prefixes:
                expanded.extend(prefixes[:3])
            if term in self.vocabulary or prefixes:
                continue
            abbreviations = sorted(
                token
                for token in self.vocabulary
                if len(token) >= 3 and term.startswith(token)
            )
            if abbreviations:
                expanded.extend(abbreviations[:3])
                fuzzy[term] = abbreviations[0]
            # ponytail: vocabulary scan is fine for schema-sized indexes; add trigram postings
            # only if a benchmark shows more than 10k relations.
            matches = heapq.nlargest(
                2,
                ((_similarity(term, token), token) for token in self.vocabulary),
            )
            for score, token in matches:
                if score >= 0.55:
                    expanded.append(token)
                    fuzzy[term] = token
        return list(dict.fromkeys(expanded)), fuzzy, acronyms

    def _rank(
        self,
        question: str,
        limit: int,
    ) -> tuple[list[tuple[float, str]], list[str], dict[str, str], dict[str, str]]:
        terms, fuzzy, acronyms = self._expanded_terms(question)
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
                score += inverse * frequency * 2.2 / denominator
            relation = self.relations[relation_id]
            relation_name = str(relation.name).lower()
            relation_name_terms = set(_tokens(relation_name))
            score += 0.75 * len(relation_name_terms & set(terms))
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
        return heapq.nlargest(limit, ranked), terms, fuzzy, acronyms

    @lru_cache(maxsize=256)
    def context(
        self,
        question: str,
        max_relations: int = 6,
        max_bytes: int = 6_144,
    ) -> dict[str, Any]:
        max_relations = max(1, min(max_relations, 12))
        max_bytes = max(2_048, min(max_bytes, 32_768))
        ranked, terms, fuzzy, acronyms = self._rank(question, max_relations * 4)
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
        subgraph = GraphEngine.join_subgraph(self.snapshot, terminal_ids)
        relation_ids = terminal_ids + [
            node for node in subgraph["nodes"] if node not in terminal_ids
        ]
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
            relations.append(
                {
                    "id": relation_id,
                    "columns": selected,
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
        response["bytes"] = size()
