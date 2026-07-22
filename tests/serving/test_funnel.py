"""The decisioning funnel: Tier-1 score auto-escalates the flagged (review/block) to Tier 2.

The whole point of the module is that a flagged transaction gets a verdict WITHOUT a human
writing a prompt — and that a cleared transaction never pays for a Tier-2 call. Both halves are
pinned here with injected score/verdict functions (no cloud).
"""

from __future__ import annotations

import pytest

from ml.serving.funnel import FLAGGED_DECISIONS, FraudFunnel


def _score(decision: str, fraud_score: float = 0.5):
    """A stand-in Tier-1 scorer returning the real serving contract shape."""

    def score_fn(transaction_id: str, card_hash: str) -> dict:
        return {
            "fraud_score": fraud_score,
            "decision_hint": decision,
            "top_features": [{"name": "amount_zscore", "value": 3.5, "contribution": 0.4}],
        }

    return score_fn


class _CountingVerdict:
    """Records every escalation so we can assert the Tier-2 call happened exactly when it should."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, transaction_id: str, card_hash: str) -> str:
        self.calls.append((transaction_id, card_hash))
        return f"VERDICT for {transaction_id}: BLOCK per AMLD Article 16."


# Literal decisions, NOT derived from FLAGGED_DECISIONS — else dropping "block" from the
# constant would silently drop the test case instead of turning it red (a tautology trap).
@pytest.mark.parametrize("decision", ["review", "block"])
def test_flagged_auto_escalates_to_verdict(decision):
    verdict = _CountingVerdict()
    funnel = FraudFunnel(_score(decision, 0.91), verdict)

    result = funnel.run("txn_demo_fraud_001", "card_demo_hi_risk")

    assert result.escalated is True
    assert result.verdict is not None and "AMLD" in result.verdict
    # The escalation is automatic — one Tier-2 call, triggered by the score alone, no prompt.
    assert verdict.calls == [("txn_demo_fraud_001", "card_demo_hi_risk")]


def test_both_flagged_decisions_are_in_the_escalation_set():
    # Pins the contract: review AND block escalate. A regression that narrows this set
    # (e.g. drops "block") turns test_flagged_auto_escalates_to_verdict[block] red too.
    assert set(FLAGGED_DECISIONS) == {"review", "block"}


def test_allow_clears_without_touching_tier_two():
    verdict = _CountingVerdict()
    funnel = FraudFunnel(_score("allow", 0.02), verdict)

    result = funnel.run("txn_demo_legit_001", "card_demo_normal")

    assert result.escalated is False
    assert result.verdict is None
    # The ~99% that clear must NEVER pay for a Tier-2 invocation — that is the funnel's economics.
    assert verdict.calls == []


def test_decision_matching_is_case_insensitive():
    verdict = _CountingVerdict()
    funnel = FraudFunnel(_score("REVIEW", 0.6), verdict)

    result = funnel.run("txn_x", "card_x")

    assert result.decision == "review"
    assert result.escalated is True


def test_result_carries_score_and_top_features_through():
    funnel = FraudFunnel(_score("block", 0.97), _CountingVerdict())

    result = funnel.run("txn_x", "card_x")

    assert result.fraud_score == pytest.approx(0.97)
    assert result.top_features[0]["name"] == "amount_zscore"
