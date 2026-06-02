"""The Staging -> Production promotion gate (pure, no MLflow).

Policy (CLAUDE.md / docs/PROJECT_PLAN.md): promote ONLY when
    AUC-ROC >= 0.92  AND  fraud-class precision >= 0.85
on the held-out test set. This module is pure so the gate is fully unit-testable; the
actual stage transition lives in `registry.py`, guarded by this decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

AUC_THRESHOLD = 0.92
FRAUD_PRECISION_THRESHOLD = 0.85

AUC_METRIC = "auc_roc"
FRAUD_PRECISION_METRIC = "fraud_precision"


@dataclass(frozen=True)
class PromotionDecision:
    """Outcome of the promotion gate."""

    promote: bool
    reason: str


def evaluate_promotion(metrics: Mapping[str, float]) -> PromotionDecision:
    """Apply the promotion policy to a metrics mapping.

    Both thresholds must be met. Missing metrics reject (fail closed).
    """
    auc = metrics.get(AUC_METRIC)
    precision = metrics.get(FRAUD_PRECISION_METRIC)
    if auc is None or precision is None:
        return PromotionDecision(
            False, f"missing required metrics: {AUC_METRIC}, {FRAUD_PRECISION_METRIC}"
        )

    failures = []
    if auc < AUC_THRESHOLD:
        failures.append(f"AUC-ROC {auc:.4f} < {AUC_THRESHOLD}")
    if precision < FRAUD_PRECISION_THRESHOLD:
        failures.append(f"fraud precision {precision:.4f} < {FRAUD_PRECISION_THRESHOLD}")

    if failures:
        return PromotionDecision(False, "; ".join(failures))
    return PromotionDecision(
        True,
        f"AUC-ROC {auc:.4f} >= {AUC_THRESHOLD} AND "
        f"fraud precision {precision:.4f} >= {FRAUD_PRECISION_THRESHOLD}",
    )
