"""Vector Search wrapper: find historically resolved cases similar to the current one.

The Vector Search index client is INJECTABLE (the embedding model + index live on
Databricks). This module builds the query, calls the index, and formats the top-k results
with their metadata into plain dicts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class VectorIndex(Protocol):
    """Subset of the Databricks Vector Search index client used here."""

    def similarity_search(
        self, *, query_text: str, columns: list[str], num_results: int
    ) -> dict[str, Any]: ...


# Columns retrieved from the case index.
RESULT_COLUMNS = ["case_id", "summary", "outcome", "disposition"]


def _num(value: object) -> float:
    """A number, or 0.0 for None/missing/unparseable. `.get(k, default)` does not cover a
    NULL VALUE, only an absent key, and a Delta row has plenty of the former."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def build_query(case: Mapping[str, Any]) -> str:
    """Turn a flagged case (features + optional note) into a retrieval query string."""
    drivers = []
    if case.get("country_mismatch"):
        drivers.append("country mismatch")
    if case.get("is_unusual_hour"):
        drivers.append("unusual hour")
    if case.get("device_seen_before") is False:
        drivers.append("new device")
    # `_num`, not `float(case.get(k, default))`: `.get(k, 0.0)` guards a MISSING key, not a
    # NULL value, and a Delta row routinely carries NULLs — `float(None)` is a TypeError that
    # crashes the tool mid-call, not a degraded query.
    if _num(case.get("amount_zscore")) >= 3.0:
        drivers.append("amount outlier")
    if _num(case.get("txn_velocity_1h")) >= 5:
        drivers.append("velocity spike")

    base = "fraud case with " + ", ".join(drivers) if drivers else "fraud case"
    note = case.get("note")
    return f"{base}. {note}" if note else base


class SimilarCaseSearch:
    """Wraps a Vector Search index to return formatted similar cases."""

    def __init__(self, index: VectorIndex, *, columns: list[str] | None = None) -> None:
        self._index = index
        self._columns = columns or RESULT_COLUMNS

    def search(self, query: str, *, num_results: int = 5) -> list[dict[str, Any]]:
        """Return up to `num_results` similar cases as metadata dicts.

        The parameter is `query`, matching the tool's declared schema in `agent.py`. It was
        `query_text`, so the declared input the LLM sends (`query`) did not name the
        parameter the implementation reads — the kind of gap the schema/implementation
        binding test now catches.
        """
        raw = self._index.similarity_search(
            query_text=query, columns=self._columns, num_results=num_results
        )
        return _format_results(raw)


def _format_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Zip the manifest columns onto each row, tolerating a width mismatch.

    `strict=True` crashed the tool on a real Vector Search response: Databricks appends a
    similarity `score` column to `data_array` that the manifest may or may not list, and a
    partial response can mismatch either way — `ValueError: zip() argument 2 is longer`.
    The test fixture hand-built the manifest to make the widths agree, so the crash was
    unreachable in tests and routine in production. `strict=False` pairs what lines up and
    keeps the extra `score` (named positionally) rather than dropping the analyst's result.
    """
    columns = [col["name"] for col in raw.get("manifest", {}).get("columns", [])]
    rows = raw.get("result", {}).get("data_array") or []
    formatted = []
    for row in rows:
        # Name any trailing unlabelled column (the similarity score) rather than losing it.
        names = columns + [f"col_{i}" for i in range(len(columns), len(row))]
        formatted.append(dict(zip(names, row, strict=False)))
    return formatted
