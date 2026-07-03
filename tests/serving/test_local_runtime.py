"""Tests for the local end-to-end funnel (metrics + demo model + streaming service).

These prove the wiring the repo lacked: a demo model trains from the simulator, the
streaming funnel scores + gates a transaction, and the Prometheus exporter emits the EXACT
metric names the committed Grafana dashboards query — all offline, no Kafka, no cloud.
"""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from agents.bedrock.eval.judge import evaluate_verdict
from agents.bedrock.guardrails.policy import GuardrailPolicy
from ml.features.schema import FEATURE_NAMES
from ml.serving.local_model import generate_labeled_dataset, train_demo_scorer
from ml.serving.metrics import ServingMetrics
from ml.serving.stream_service import (
    CardHistoryStore,
    build_stub_verdict,
    process_transaction,
)


@pytest.fixture(scope="module")
def scorer():
    return train_demo_scorer(max_records=400, seed=7)


def _txn(txn_id: str, **over: object) -> dict:
    base = {
        "transaction_id": txn_id,
        "timestamp": "2026-01-01T13:00:00+00:00",
        "amount": 50.0,
        "merchant_id": "M00001",
        "card_hash": "cardA",
        "device_id": "D00001",
        "ip_country": "DE",
        "mcc_code": "5411",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# Demo model bootstrap
# --------------------------------------------------------------------------- #
def test_generated_dataset_has_feature_parity_and_both_classes():
    features, labels = generate_labeled_dataset(max_records=300, seed=7)
    assert list(features.columns) == list(FEATURE_NAMES)  # the parity invariant
    assert len(features) == 300
    assert labels.sum() > 0 and labels.sum() < len(labels)  # both classes present


def test_demo_scorer_scores_in_contract(scorer):
    features, _ = generate_labeled_dataset(max_records=50, seed=7)
    out = scorer.score(features.iloc[0].to_dict())
    assert set(out) == {
        "fraud_score",
        "model_version",
        "threshold",
        "decision_hint",
        "top_features",
    }
    assert 0.0 <= out["fraud_score"] <= 1.0
    assert out["decision_hint"] in {"allow", "review", "block"}


# --------------------------------------------------------------------------- #
# The funnel (process_transaction)
# --------------------------------------------------------------------------- #
def test_process_scores_a_normal_transaction(scorer):
    m = ServingMetrics(registry=CollectorRegistry())
    result = process_transaction(_txn("t1"), CardHistoryStore(), scorer, GuardrailPolicy(), m)
    assert result["status"] == "scored"
    assert 0.0 <= result["fraud_score"] <= 1.0
    assert result["decision"] in {"allow", "review", "block"}


def test_process_quarantines_a_bad_timestamp(scorer):
    registry = CollectorRegistry()
    m = ServingMetrics(registry=registry)
    result = process_transaction(
        _txn("t2", timestamp="not-a-date"), CardHistoryStore(), scorer, GuardrailPolicy(), m
    )
    assert result["status"] == "quarantined"
    value = registry.get_sample_value(
        "fintelliguard_quarantined_total",
        {"endpoint": "fintelliguard-fraud-score", "environment": "local"},
    )
    assert value == 1.0  # the quarantine counter incremented exactly once


def test_flagged_transaction_runs_the_real_verdict_gate(scorer):
    # A synthetic high-risk scored result forces the Tier-2 path deterministically.
    scored = {
        "fraud_score": 0.97,
        "model_version": "v",
        "threshold": 0.7,
        "decision_hint": "block",
        "top_features": [{"name": "country_mismatch", "value": True, "contribution": 0.5}],
    }
    verdict, context = build_stub_verdict(scored)
    gate = evaluate_verdict(verdict, context)
    assert gate.accepted is True, gate.failures
    # And the guardrail passes the (PII-free, grounded) reasoning.
    assert GuardrailPolicy().evaluate_output(verdict["reasoning"], grounding_score=1.0).allowed


def test_history_is_bounded_and_ordered():
    store = CardHistoryStore(cap=3)
    for i in range(5):
        store.append("c", {"transaction_id": f"t{i}", "timestamp": f"2026-01-01T0{i}:00:00+00:00"})
    kept = store.get("c")
    assert len(kept) == 3  # ring buffer capped
    assert [h["transaction_id"] for h in kept] == ["t2", "t3", "t4"]  # newest kept, in order


# --------------------------------------------------------------------------- #
# Prometheus exporter — the metric names MUST match the dashboards
# --------------------------------------------------------------------------- #
def test_exporter_emits_the_dashboard_metric_names():
    registry = CollectorRegistry()
    m = ServingMetrics(registry=registry, model_version="fraud-xgb:test")
    m.observe_request(0.004, "review")
    m.observe_score(0.81)
    m.record_verdict(True)
    m.record_guardrail_block("PII")
    m.record_quarantine()

    exposition = generate_latest(registry).decode("utf-8")
    # The three the Grafana dashboards (dashboards/serving_latency.json) query:
    assert "model_serving_request_duration_seconds_bucket" in exposition
    assert "model_serving_requests_total" in exposition
    assert "model_serving_build_info" in exposition
    # The fraud-specific series + the correct fixed label:
    assert "fintelliguard_fraud_score_bucket" in exposition
    assert "fintelliguard_verdict_gate_total" in exposition
    assert 'endpoint="fintelliguard-fraud-score"' in exposition
