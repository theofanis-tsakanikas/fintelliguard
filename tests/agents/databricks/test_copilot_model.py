"""The copilot orchestration core: real tool calls + a grounded LLM synthesis."""

from __future__ import annotations

import pytest

from agents.databricks.copilot_model import CopilotModel, build_investigation_prompt

_SCORE = {
    "fraud_score": 0.88,
    "decision_hint": "review",
    "threshold": 0.7,
    "top_features": [
        {"name": "txn_velocity_1h", "value": 9, "contribution": 1.2},
        {"name": "country_mismatch", "value": True, "contribution": 0.9},
    ],
}
_CASES = [
    {
        "case_id": "c-1",
        "outcome": "confirmed_fraud",
        "disposition": "blocked",
        "summary": "velocity spike",
    },
    {"case_id": "c-2", "outcome": "legit", "disposition": "released", "summary": "traveller"},
]


class _ScoreStub:
    def __init__(self):
        self.calls = []

    def get_fraud_score(self, transaction_id, card_hash):
        self.calls.append((transaction_id, card_hash))
        return _SCORE


class _SearchStub:
    def __init__(self):
        self.calls = []

    def search(self, query, *, num_results=5):
        self.calls.append((query, num_results))
        return _CASES


def test_investigate_calls_both_tools_and_returns_the_grounded_brief():
    score, search = _ScoreStub(), _SearchStub()
    captured_prompt = {}

    def llm(prompt):
        captured_prompt["p"] = prompt
        return "Investigation summary."

    copilot = CopilotModel(score, search, llm)
    result = copilot.investigate(
        query="why was this flagged?", transaction_id="txn-1", card_hash="card-a", num_cases=3
    )

    # Both tools were actually invoked with the right arguments.
    assert score.calls == [("txn-1", "card-a")]
    assert search.calls == [("why was this flagged?", 3)]
    # The brief carries the LLM answer AND the raw evidence for auditing.
    assert result["answer"] == "Investigation summary."
    assert result["fraud_score"] == 0.88
    assert result["decision_hint"] == "review"
    assert result["similar_cases"] == _CASES
    assert result["tools_used"] == ["get_fraud_score", "search_similar_cases"]


def test_the_llm_prompt_is_grounded_in_the_tool_evidence():
    """The summary can only be as grounded as the evidence block it is handed."""
    prompt = build_investigation_prompt("why flagged?", _SCORE, _CASES)
    assert "fraud_score=0.88" in prompt
    assert "txn_velocity_1h" in prompt
    assert "c-1" in prompt and "confirmed_fraud" in prompt
    assert "ONLY the evidence" in prompt, "the prompt must forbid inventing facts"


def test_empty_case_list_does_not_break_the_prompt():
    prompt = build_investigation_prompt("q", _SCORE, [])
    assert "no similar resolved cases" in prompt


def test_investigate_refuses_an_empty_question():
    copilot = CopilotModel(_ScoreStub(), _SearchStub(), lambda _p: "x")
    with pytest.raises(ValueError, match="analyst question"):
        copilot.investigate(query="", transaction_id="t", card_hash="c")
