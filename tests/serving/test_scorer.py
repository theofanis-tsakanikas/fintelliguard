"""Scorer: contract fidelity, feature parity, decision bands, per-prediction top_features."""

from __future__ import annotations

import pytest

from ml.features.schema import FEATURE_NAMES
from ml.serving.scorer import (
    CONTRACT_KEYS,
    FraudScorer,
    ScoringConfig,
    decision_hint,
)

from .conftest import SAMPLE_FEATURES


def test_output_matches_bedrock_contract(trained_xgb):
    result = FraudScorer(trained_xgb, ScoringConfig(model_version="fraud-xgb:23")).score(
        SAMPLE_FEATURES
    )

    assert tuple(result.keys()) == CONTRACT_KEYS
    assert isinstance(result["fraud_score"], float) and 0.0 <= result["fraud_score"] <= 1.0
    assert result["model_version"] == "fraud-xgb:23"
    assert isinstance(result["threshold"], float)
    assert result["decision_hint"] in {"allow", "review", "block"}

    assert isinstance(result["top_features"], list) and result["top_features"]
    for item in result["top_features"]:
        assert set(item.keys()) == {"name", "value", "contribution"}
        assert item["name"] in FEATURE_NAMES
        assert isinstance(item["contribution"], float)


def test_reported_threshold_is_review_threshold(trained_xgb):
    config = ScoringConfig(review_threshold=0.6, block_threshold=0.95)
    result = FraudScorer(trained_xgb, config).score(SAMPLE_FEATURES)
    assert result["threshold"] == 0.6


def test_feature_parity_missing_and_extra(trained_xgb):
    scorer = FraudScorer(trained_xgb)

    missing = {k: v for k, v in SAMPLE_FEATURES.items() if k != "amount_zscore"}
    with pytest.raises(ValueError, match="missing=\\['amount_zscore'\\]"):
        scorer.score(missing)

    extra = {**SAMPLE_FEATURES, "not_a_feature": 1.0}
    with pytest.raises(ValueError, match="extra=\\['not_a_feature'\\]"):
        scorer.score(extra)


def test_decision_bands():
    config = ScoringConfig(review_threshold=0.70, block_threshold=0.90)
    assert decision_hint(0.10, config) == "allow"
    assert decision_hint(0.699, config) == "allow"
    assert decision_hint(0.70, config) == "review"
    assert decision_hint(0.85, config) == "review"
    assert decision_hint(0.90, config) == "block"
    assert decision_hint(0.99, config) == "block"


def test_top_features_ranked_by_abs_contribution_with_native_values(trained_xgb):
    config = ScoringConfig(top_n=4)
    result = FraudScorer(trained_xgb, config).score(SAMPLE_FEATURES)
    top = result["top_features"]

    assert len(top) == 4
    # Ranked by descending |contribution|.
    magnitudes = [abs(item["contribution"]) for item in top]
    assert magnitudes == sorted(magnitudes, reverse=True)
    # Values match the input and keep their native types.
    for item in top:
        assert item["value"] == SAMPLE_FEATURES[item["name"]]
    assert isinstance(SAMPLE_FEATURES["country_mismatch"], bool)  # sanity: a bool feature exists


def test_scoring_config_validates_bands():
    with pytest.raises(ValueError, match="review_threshold"):
        ScoringConfig(review_threshold=0.9, block_threshold=0.5)
    with pytest.raises(ValueError, match="top_n"):
        ScoringConfig(top_n=0)
