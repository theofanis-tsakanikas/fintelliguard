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


def build_query(case: Mapping[str, Any]) -> str:
    """Turn a flagged case (features + optional note) into a retrieval query string."""
    drivers = []
    if case.get("country_mismatch"):
        drivers.append("country mismatch")
    if case.get("is_unusual_hour"):
        drivers.append("unusual hour")
    if case.get("device_seen_before") is False:
        drivers.append("new device")
    if float(case.get("amount_zscore", 0.0)) >= 3.0:
        drivers.append("amount outlier")
    if int(case.get("txn_velocity_1h", 0)) >= 5:
        drivers.append("velocity spike")

    base = "fraud case with " + ", ".join(drivers) if drivers else "fraud case"
    note = case.get("note")
    return f"{base}. {note}" if note else base


class SimilarCaseSearch:
    """Wraps a Vector Search index to return formatted similar cases."""

    def __init__(self, index: VectorIndex, *, columns: list[str] | None = None) -> None:
        self._index = index
        self._columns = columns or RESULT_COLUMNS

    def search(self, query_text: str, *, num_results: int = 5) -> list[dict[str, Any]]:
        """Return up to `num_results` similar cases as metadata dicts."""
        raw = self._index.similarity_search(
            query_text=query_text, columns=self._columns, num_results=num_results
        )
        return _format_results(raw)


def _format_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    columns = [col["name"] for col in raw.get("manifest", {}).get("columns", [])]
    rows = raw.get("result", {}).get("data_array") or []
    return [dict(zip(columns, row)) for row in rows]
