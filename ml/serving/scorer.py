"""Pure scoring wrapper implementing the `get_fraud_score()` contract.

This is the cross-cloud contract Bedrock calls (docs/bedrock-integration.md). Input is a
feature vector of EXACTLY the canonical 15 features (parity-checked); output is the tool
output schema:

    {fraud_score, model_version, threshold, decision_hint, top_features[]}

`top_features` are PER-PREDICTION contributions (why THIS transaction scored as it did) —
exact TreeSHAP values from XGBoost's built-in `pred_contribs` (no extra dependency), not
global importance. Names are the canonical 15; `value` keeps the feature's native type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

from ml.features.schema import FEATURE_NAMES

# The exact output contract keys (docs/bedrock-integration.md).
CONTRACT_KEYS = ("fraud_score", "model_version", "threshold", "decision_hint", "top_features")
DECISION_ALLOW = "allow"
DECISION_REVIEW = "review"
DECISION_BLOCK = "block"


@dataclass(frozen=True)
class ScoringConfig:
    """Decision bands + identity for scoring.

    `threshold` reported in the contract is the review threshold (the Tier-2 trigger).
    """

    review_threshold: float = 0.70
    block_threshold: float = 0.90
    top_n: int = 5
    model_version: str = "fraud-xgb:local"

    def __post_init__(self) -> None:
        if not 0.0 <= self.review_threshold <= self.block_threshold <= 1.0:
            raise ValueError("require 0 <= review_threshold <= block_threshold <= 1")
        if self.top_n < 1:
            raise ValueError("top_n must be >= 1")


def decision_hint(score: float, config: ScoringConfig) -> str:
    """Map a score to allow / review / block per the configured bands."""
    if score >= config.block_threshold:
        return DECISION_BLOCK
    if score >= config.review_threshold:
        return DECISION_REVIEW
    return DECISION_ALLOW


def _to_native(value: Any) -> Any:
    """Coerce numpy scalars to native Python types (JSON-friendly, keeps bool/int/float)."""
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


class FraudScorer:
    """Wrap a trained XGBoost classifier in the `get_fraud_score()` contract."""

    def __init__(self, model: xgb.XGBClassifier, config: ScoringConfig | None = None) -> None:
        self.model = model
        self.config = config or ScoringConfig()
        self._booster = model.get_booster()

    def score(self, features: dict[str, Any]) -> dict[str, Any]:
        """Return the contract dict for one feature vector (exactly the canonical 15)."""
        self._check_parity(features)

        row = pd.DataFrame(
            [[float(features[name]) for name in FEATURE_NAMES]], columns=list(FEATURE_NAMES)
        )
        fraud_score = float(self.model.predict_proba(row)[0][1])

        # Per-prediction TreeSHAP contributions; last column is the bias term.
        contributions = self._booster.predict(xgb.DMatrix(row), pred_contribs=True)[0][:-1]

        return {
            "fraud_score": fraud_score,
            "model_version": self.config.model_version,
            "threshold": self.config.review_threshold,
            "decision_hint": decision_hint(fraud_score, self.config),
            "top_features": self._top_features(features, contributions),
        }

    def _check_parity(self, features: dict[str, Any]) -> None:
        provided = set(features)
        expected = set(FEATURE_NAMES)
        if provided != expected:
            missing = sorted(expected - provided)
            extra = sorted(provided - expected)
            raise ValueError(f"feature parity violation: missing={missing} extra={extra}")

    def _top_features(
        self, features: dict[str, Any], contributions: np.ndarray
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            zip(FEATURE_NAMES, contributions, strict=True),
            key=lambda pair: abs(pair[1]),
            reverse=True,
        )
        return [
            {"name": name, "value": _to_native(features[name]), "contribution": float(contribution)}
            for name, contribution in ranked[: self.config.top_n]
        ]
