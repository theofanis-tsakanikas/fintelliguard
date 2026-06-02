"""get_fraud_score tool reuses the ml/serving scorer — same contract."""

from __future__ import annotations

from agents.databricks.tools.get_fraud_score import FraudScoreTool
from ml.serving.scorer import CONTRACT_KEYS, FraudScorer


def test_tool_returns_same_contract_as_scorer(trained_xgb, sample_features):
    scorer = FraudScorer(trained_xgb)
    tool = FraudScoreTool(scorer.score)

    result = tool.get_fraud_score(sample_features)
    assert tuple(result.keys()) == CONTRACT_KEYS
    assert result == scorer.score(sample_features)
    assert result["top_features"]
