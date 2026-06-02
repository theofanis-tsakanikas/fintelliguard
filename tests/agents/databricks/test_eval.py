"""Eval dataset coverage + the tool-selection scoring harness."""

from __future__ import annotations

import pytest

from agents.databricks.agent import GET_FRAUD_SCORE, QUERY_LAKEHOUSE, SEARCH_SIMILAR_CASES
from agents.databricks.eval.dataset import eval_dataset
from agents.databricks.eval.scoring import keyword_router, score_tool_selection

_TOOLS = {QUERY_LAKEHOUSE, SEARCH_SIMILAR_CASES, GET_FRAUD_SCORE}


def test_dataset_covers_all_tools_with_valid_labels():
    cases = eval_dataset()
    assert len(cases) >= 9
    assert {c.expected_tool for c in cases} == _TOOLS
    assert all(c.expected_tool in _TOOLS for c in cases)


def test_perfect_router_scores_one():
    cases = eval_dataset()
    expected = {c.question: c.expected_tool for c in cases}
    report = score_tool_selection(cases, lambda q: expected[q])
    assert report.accuracy == 1.0
    assert set(report.per_tool) == _TOOLS
    assert all(bucket["accuracy"] == 1.0 for bucket in report.per_tool.values())


def test_always_wrong_router_scores_zero():
    cases = eval_dataset()
    report = score_tool_selection(cases, lambda q: "not_a_tool")
    assert report.accuracy == 0.0
    assert report.correct == 0


def test_keyword_baseline_router_has_strong_signal():
    cases = eval_dataset()
    report = score_tool_selection(cases, keyword_router)
    # Crafted dataset: the deterministic baseline should route almost everything correctly.
    assert report.accuracy >= 0.8
    assert set(report.per_tool) == _TOOLS
    assert report.total == len(cases)


def test_empty_dataset_raises():
    with pytest.raises(ValueError, match="empty dataset"):
        score_tool_selection([], keyword_router)
