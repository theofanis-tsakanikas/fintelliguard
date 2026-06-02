"""Model serving layer — the `get_fraud_score()` contract wrapper + MLflow pyfunc.

Pure scoring is locally testable; Mosaic AI Model Serving deployment and the online
Feature Store lookup are deferred to deploy.
"""

from __future__ import annotations

from ml.serving.endpoint import FraudScoringModel, log_scoring_model
from ml.serving.scorer import (
    CONTRACT_KEYS,
    FraudScorer,
    ScoringConfig,
    decision_hint,
)

__all__ = [
    "CONTRACT_KEYS",
    "FraudScorer",
    "FraudScoringModel",
    "ScoringConfig",
    "decision_hint",
    "log_scoring_model",
]
