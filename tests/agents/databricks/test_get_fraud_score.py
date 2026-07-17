"""get_fraud_score tool reuses the ml/serving scorer — same contract."""

from __future__ import annotations

import pytest

from agents.databricks.tools.get_fraud_score import FraudScoreTool
from ml.serving.scorer import CONTRACT_KEYS, FraudScorer


def test_tool_resolves_ids_to_features_then_returns_the_scorer_contract(
    trained_xgb, sample_features
):
    scorer = FraudScorer(trained_xgb)
    # The Feature Store seam: id -> features. A stub here; the online store in production.
    lookup = lambda transaction_id, card_hash: sample_features  # noqa: E731
    tool = FraudScoreTool(scorer.score, lookup)

    result = tool.get_fraud_score(transaction_id="t1", card_hash="c1")
    assert tuple(result.keys()) == CONTRACT_KEYS
    assert result == scorer.score(sample_features)
    assert result["top_features"]


def test_tool_refuses_to_score_a_transaction_with_no_features(trained_xgb):
    """An empty feature vector must not silently produce a fabricated score.

    The tool used to take a feature vector directly, so an empty dict was scored as if it
    were a real transaction. Now the lookup returning nothing is an error, not a 0.5.
    """
    from agents.databricks.tools.get_fraud_score import FeatureLookupError

    tool = FraudScoreTool(FraudScorer(trained_xgb).score, lambda t, c: {})
    with pytest.raises(FeatureLookupError, match="no features"):
        tool.get_fraud_score(transaction_id="missing", card_hash="c1")


def test_tool_refuses_partial_ids(trained_xgb, sample_features):
    """Both ids are required — the copilot cannot resolve features from half a key."""
    from agents.databricks.tools.get_fraud_score import FeatureLookupError

    tool = FraudScoreTool(FraudScorer(trained_xgb).score, lambda t, c: sample_features)
    with pytest.raises(FeatureLookupError):
        tool.get_fraud_score(transaction_id="", card_hash="c1")
