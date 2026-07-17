"""The tool-selection harness, and the keyword baseline measured HONESTLY.

Two tests here used to be closed loops, and the second one hid a real number:

* `test_perfect_router_scores_one` built `expected = {q: tool}` from the labels and routed
  with `lambda q: expected[q]` — it asserted a dict returns what was put in it. It tested
  `dict`, not the harness.
* `test_keyword_baseline_router_has_strong_signal` asserted accuracy >= 0.8 against
  `eval_dataset()` — the very set the cues in `scoring.py` were written from. In-sample it
  scores 1.00; on held-out phrasings it scores 0.38, barely above the 0.33 of routing
  everything to one tool. The 0.8 was the closed loop reporting itself.

A keyword matcher SHOULD be weak on held-out — it is a FLOOR for the production LLM router
to beat, not the router. So the honest tests are: the harness scores a genuinely
independent router correctly, and the baseline's held-out number is measured and asserted
to be a floor (better than chance, and NOT claimed to be good), not inflated by the loop.
"""

from __future__ import annotations

import pytest

from agents.databricks.agent import GET_FRAUD_SCORE, QUERY_LAKEHOUSE, SEARCH_SIMILAR_CASES
from agents.databricks.eval.dataset import eval_dataset, held_out_dataset
from agents.databricks.eval.scoring import keyword_router, score_tool_selection

_TOOLS = {QUERY_LAKEHOUSE, SEARCH_SIMILAR_CASES, GET_FRAUD_SCORE}
_RANDOM_BASELINE = 1 / 3  # three tools, so routing everything to one scores ~0.33


def test_dataset_and_held_out_cover_all_tools():
    for cases in (eval_dataset(), held_out_dataset()):
        assert {c.expected_tool for c in cases} == _TOOLS, (
            "a split that omits a tool cannot measure routing to it"
        )


def test_the_two_splits_share_no_questions():
    """The held-out set must be genuinely held out, or it is the training set again."""
    in_sample = {c.question for c in eval_dataset()}
    held_out = {c.question for c in held_out_dataset()}
    assert not (in_sample & held_out), (
        f"held-out questions leaked from the in-sample set: {in_sample & held_out}"
    )


def test_the_harness_scores_a_router_that_reads_the_question():
    """The harness works — tested with a router that actually inspects the input.

    NOT `lambda q: labels[q]`: a dict keyed on the question returns the label whatever the
    router logic is, so it scores 1.0 for a harness that does nothing. This router keys on a
    word that is really in the questions, so the harness has to route and compare.
    """

    def word_router(question: str) -> str:
        q = question.lower()
        if "similar" in q or "precedent" in q or "resembl" in q:
            return SEARCH_SIMILAR_CASES
        if "why" in q or "drove" in q or "explain" in q:
            return GET_FRAUD_SCORE
        return QUERY_LAKEHOUSE

    report = score_tool_selection(eval_dataset(), word_router)
    assert 0.0 < report.accuracy <= 1.0
    assert set(report.per_tool) == _TOOLS
    assert report.total == len(eval_dataset())


def test_the_harness_scores_zero_for_an_always_wrong_router():
    report = score_tool_selection(eval_dataset(), lambda q: "not_a_tool")
    assert report.accuracy == 0.0
    assert report.correct == 0


def test_the_keyword_baseline_is_a_floor_not_a_router():
    """Measured on HELD-OUT phrasings, and asserted for what a floor is.

    In-sample accuracy is meaningless — the cues were written from that set — so this asserts
    on `held_out_dataset()`. A keyword baseline beats chance and is nowhere near good; both
    halves are the claim. If a change makes it *look* good on held-out, that is a signal the
    held-out set has drifted toward the cues, not that the baseline improved.
    """
    in_sample = score_tool_selection(eval_dataset(), keyword_router).accuracy
    held_out = score_tool_selection(held_out_dataset(), keyword_router).accuracy

    assert in_sample == 1.0, "the baseline should fit the set its cues were written from"
    assert held_out > _RANDOM_BASELINE, (
        f"held-out {held_out:.2f} is at or below chance ({_RANDOM_BASELINE:.2f}) — the "
        "baseline carries no signal at all"
    )
    assert held_out < 0.8, (
        f"held-out {held_out:.2f} is suspiciously high for a keyword matcher — either the "
        "held-out set has drifted toward the cues, or this is being read as a quality claim. "
        "It is a floor for the LLM router to beat, not the router."
    )


def test_empty_dataset_raises():
    with pytest.raises(ValueError, match="empty dataset"):
        score_tool_selection([], keyword_router)
