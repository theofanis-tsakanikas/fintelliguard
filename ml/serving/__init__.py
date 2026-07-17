"""Model serving layer — the `get_fraud_score()` contract wrapper + MLflow pyfunc.

Pure scoring is locally testable; Mosaic AI Model Serving deployment and the online
Feature Store lookup are deferred to deploy.

`endpoint` (the MLflow pyfunc wrapper) is imported LAZILY: it pulls in `mlflow`, which the
self-sufficient local runtime (`.[local]`, the docker funnel) deliberately does not ship. A
`from ml.serving import FraudScoringModel` still works wherever mlflow is installed, but
`python -m ml.serving.stream_service` — the local scorer — no longer has to import mlflow
just to load this package. Eagerly importing it here is what broke the one-command funnel:
the package `__init__` ran first and died on `ModuleNotFoundError: mlflow` before the pure
scorer ever loaded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ml.serving.scorer import (
    CONTRACT_KEYS,
    FraudScorer,
    ScoringConfig,
    decision_hint,
)

if TYPE_CHECKING:
    from ml.serving.endpoint import FraudScoringModel, log_scoring_model

_LAZY = {"FraudScoringModel", "log_scoring_model"}

__all__ = [
    "CONTRACT_KEYS",
    "FraudScorer",
    "FraudScoringModel",
    "ScoringConfig",
    "decision_hint",
    "log_scoring_model",
]


def __getattr__(name: str) -> object:
    """Resolve the mlflow-backed endpoint symbols on first access, not at import.

    PEP 562 module-level hook. Kept to exactly the two names that need mlflow so a typo
    still raises `AttributeError` rather than silently importing something unexpected.
    """
    if name in _LAZY:
        from ml.serving import endpoint

        return getattr(endpoint, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
