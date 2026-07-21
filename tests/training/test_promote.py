"""Promotion gate — both branches, controlled metrics, correct reasons."""

from __future__ import annotations

from ml.training.promote import (
    AUC_THRESHOLD,
    FRAUD_PRECISION_THRESHOLD,
    evaluate_promotion,
)


def test_promote_when_both_thresholds_met():
    decision = evaluate_promotion({"auc_roc": 0.95, "fraud_precision": 0.88})
    assert decision.promote is True
    assert "AUC-ROC" in decision.reason and "fraud precision" in decision.reason


def test_promote_at_exact_thresholds():
    decision = evaluate_promotion(
        {"auc_roc": AUC_THRESHOLD, "fraud_precision": FRAUD_PRECISION_THRESHOLD}
    )
    assert decision.promote is True


def test_reject_when_auc_too_low():
    # Below the AUC floor but above the precision floor — reference the constants so this
    # stays correct if the policy moves (it moved 0.92 -> 0.83).
    decision = evaluate_promotion(
        {"auc_roc": AUC_THRESHOLD - 0.05, "fraud_precision": FRAUD_PRECISION_THRESHOLD + 0.02}
    )
    assert decision.promote is False
    assert "AUC-ROC" in decision.reason
    assert "fraud precision" not in decision.reason  # precision passed


def test_reject_when_precision_too_low():
    decision = evaluate_promotion({"auc_roc": 0.95, "fraud_precision": 0.80})
    assert decision.promote is False
    assert "fraud precision" in decision.reason
    assert "AUC-ROC" not in decision.reason  # auc passed


def test_reject_when_both_low_lists_both():
    decision = evaluate_promotion({"auc_roc": 0.50, "fraud_precision": 0.50})
    assert decision.promote is False
    assert "AUC-ROC" in decision.reason and "fraud precision" in decision.reason


def test_reject_when_metrics_missing():
    decision = evaluate_promotion({"auc_roc": 0.99})
    assert decision.promote is False
    assert "missing" in decision.reason
