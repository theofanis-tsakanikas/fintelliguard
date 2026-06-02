"""Vector Search wrapper against a mocked index (embedding + index are cloud)."""

from __future__ import annotations

from agents.databricks.tools.search_similar_cases import SimilarCaseSearch, build_query


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
