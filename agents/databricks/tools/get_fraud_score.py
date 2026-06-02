"""The copilot's `get_fraud_score` tool — the SAME contract/endpoint Bedrock uses.

Reuses the `ml/serving` scorer contract: the copilot asks the model why a transaction was
flagged. The scoring function is injected (the live Mosaic endpoint in prod, the local
`ml/serving` scorer in tests), so contract fidelity is testable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FraudScoreTool:
    """Returns the get_fraud_score contract for a feature vector."""

    def __init__(self, score_fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._score_fn = score_fn

    def get_fraud_score(self, features: dict[str, Any]) -> dict[str, Any]:
        """Score a feature vector, returning the get_fraud_score contract."""
        return self._score_fn(features)
