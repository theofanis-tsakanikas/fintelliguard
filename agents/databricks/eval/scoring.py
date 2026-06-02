"""Local tool-selection-accuracy scoring harness.

Given the labeled dataset and a router (`question -> tool name`), compute overall
tool-selection accuracy plus a per-tool breakdown. The production router is the agent's
LLM (deferred); `keyword_router` is a deterministic baseline used to exercise the harness
offline — it is NOT the production router.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agents.databricks.agent import GET_FRAUD_SCORE, QUERY_LAKEHOUSE, SEARCH_SIMILAR_CASES
from agents.databricks.eval.dataset import EvalCase


@dataclass(frozen=True)
class ToolSelectionReport:
    """Overall accuracy + per-tool breakdown (keyed by expected tool)."""

    total: int
    correct: int
    accuracy: float
    per_tool: dict[str, dict[str, float]]


def score_tool_selection(
    cases: Sequence[EvalCase], predict_tool: Callable[[str], str]
) -> ToolSelectionReport:
    """Score a router against the labeled cases (expected vs predicted tool)."""
    if not cases:
        raise ValueError("cannot score an empty dataset")

    per_tool: dict[str, dict[str, float]] = {}
    correct = 0
    for case in cases:
        predicted = predict_tool(case.question)
        hit = int(predicted == case.expected_tool)
        correct += hit
        bucket = per_tool.setdefault(case.expected_tool, {"total": 0.0, "correct": 0.0})
        bucket["total"] += 1
        bucket["correct"] += hit

    for bucket in per_tool.values():
        bucket["accuracy"] = bucket["correct"] / bucket["total"]

    return ToolSelectionReport(
        total=len(cases),
        correct=correct,
        accuracy=correct / len(cases),
        per_tool=per_tool,
    )


# Keyword cues -> tool. Order matters: similarity and "why" cues win over the default.
_SIMILARITY_CUES = ("similar", "like this", "precedent", "resembl", "pattern")
_SCORE_CUES = ("why", "explain", "drove", "score", "flagged")


def keyword_router(question: str) -> str:
    """Deterministic baseline router (offline harness only — not the production router)."""
    text = question.lower()
    if any(cue in text for cue in _SIMILARITY_CUES):
        return SEARCH_SIMILAR_CASES
    if any(cue in text for cue in _SCORE_CUES):
        return GET_FRAUD_SCORE
    return QUERY_LAKEHOUSE
