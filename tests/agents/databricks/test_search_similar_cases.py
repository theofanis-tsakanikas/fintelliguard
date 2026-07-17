"""Vector Search wrapper against a mocked index (embedding + index are cloud)."""

from __future__ import annotations

from agents.databricks.tools.search_similar_cases import (
    SimilarCaseSearch,
    _format_results,
    build_query,
)


class _FakeIndex:
    """Mimics the Databricks Vector Search similarity_search response shape."""

    def __init__(self):
        self.calls = []

    def similarity_search(self, *, query_text, columns, num_results):
        self.calls.append(
            {"query_text": query_text, "columns": columns, "num_results": num_results}
        )
        return {
            "manifest": {"columns": [{"name": c} for c in [*columns, "score"]]},
            "result": {
                "data_array": [
                    ["case-1", "country mismatch + outlier", "fraud", "blocked", 0.93],
                    ["case-2", "velocity spike", "fraud", "blocked", 0.88],
                ]
            },
        }


def test_build_query_summarizes_case_drivers():
    query = build_query(
        {
            "country_mismatch": True,
            "is_unusual_hour": True,
            "amount_zscore": 4.0,
            "note": "card abroad",
        }
    )
    assert "country mismatch" in query
    assert "unusual hour" in query
    assert "amount outlier" in query
    assert query.endswith("card abroad")


def test_search_formats_topk_with_metadata():
    index = _FakeIndex()
    search = SimilarCaseSearch(index)
    results = search.search("fraud case with country mismatch", num_results=2)

    assert index.calls[0]["num_results"] == 2
    assert index.calls[0]["query_text"] == "fraud case with country mismatch"
    assert results == [
        {
            "case_id": "case-1",
            "summary": "country mismatch + outlier",
            "outcome": "fraud",
            "disposition": "blocked",
            "score": 0.93,
        },
        {
            "case_id": "case-2",
            "summary": "velocity spike",
            "outcome": "fraud",
            "disposition": "blocked",
            "score": 0.88,
        },
    ]


def test_search_handles_empty_results():
    class _Empty:
        def similarity_search(self, *, query_text, columns, num_results):
            return {
                "manifest": {"columns": [{"name": c} for c in columns]},
                "result": {"data_array": []},
            }

    assert SimilarCaseSearch(_Empty()).search("anything") == []


def test_build_query_survives_a_null_feature_value():
    """A Delta NULL is not a missing key. `float(None)` is a crash, not a degraded query.

    `.get("amount_zscore", 0.0)` returns the default only when the key is ABSENT; a present
    key with a NULL value returns None, and `float(None)` raised mid-call. Delta rows carry
    NULLs routinely.
    """
    query = build_query({"amount_zscore": None, "txn_velocity_1h": None, "country_mismatch": True})
    assert "country mismatch" in query  # the non-null driver still lands
    assert isinstance(query, str)


def test_format_results_survives_an_extra_score_column():
    """Databricks appends a similarity score the manifest may not list.

    `strict=True` turned that into `ValueError: zip() argument 2 is longer than argument 1`
    — an uncaught crash in the analyst's tool call. The fixture used to hand-build the
    manifest to make the widths agree, so the crash was unreachable in tests and routine in
    production.
    """
    raw = {
        "manifest": {"columns": [{"name": "case_id"}, {"name": "summary"}]},
        "result": {"data_array": [["c1", "velocity spike", 0.93]]},  # 3 values, 2 columns
    }
    formatted = _format_results(raw)
    assert formatted[0]["case_id"] == "c1"
    assert formatted[0]["summary"] == "velocity spike"
    assert 0.93 in formatted[0].values()  # the score is kept, not dropped or crashed on
