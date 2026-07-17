"""Tests for the local end-to-end funnel (metrics + demo model + streaming service).

These prove the wiring the repo lacked: a demo model trains from the simulator, the
streaming funnel scores + gates a transaction, and the Prometheus exporter emits the EXACT
metric names the committed Grafana dashboards query — all offline, no Kafka, no cloud.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from agents.bedrock.eval.decision_log import MemorySink
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
def demo():
    return train_demo_scorer(max_records=400, seed=7)


@pytest.fixture(scope="module")
def scorer(demo):
    return demo.scorer


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
    assert labels.sum() > 0 and labels.sum() < len(labels)  # both classes present


def test_generated_dataset_has_no_dead_features():
    """Every feature the demo model trains on must vary.

    `merchant_risk_score` was 0.0 and `card_age_days` was 0 on every row, so the model was
    fitted on two constants and the funnel served two constants — consistent, and both
    wrong. No test looked, because none ever asserted a stream feature's range.
    """
    features, _labels = generate_labeled_dataset(max_records=600, seed=7)
    dead = [name for name in FEATURE_NAMES if features[name].nunique() <= 1]
    assert not dead, f"features that never vary across 600 transactions: {dead}"


def test_demo_scorer_is_better_than_a_coin_flip(demo):
    """A held-out AUC, on rows the model has never seen.

    The fit used to be on 100% of the data with no split, and the only assertion was
    `all(0.0 <= v <= 1.0 for v in metrics.values())` — true of a model that predicts 0.5
    forever.
    """
    assert demo.holdout_auc > 0.6, (
        f"held-out AUC {demo.holdout_auc:.3f} is close to chance — the demo model that "
        "scores the local funnel has not learned the fraud signal"
    )


def test_demo_scorer_scores_in_contract(scorer):
    features, _labels = generate_labeled_dataset(max_records=50, seed=7)
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
    result = process_transaction(
        _txn("t1"),
        CardHistoryStore(),
        scorer,
        GuardrailPolicy(),
        m,
    )
    assert result["status"] == "scored"
    assert 0.0 <= result["fraud_score"] <= 1.0
    assert result["decision"] in {"allow", "review", "block"}


def test_process_quarantines_a_bad_timestamp(scorer):
    registry = CollectorRegistry()
    m = ServingMetrics(registry=registry)
    result = process_transaction(
        _txn("t2", timestamp="not-a-date"),
        CardHistoryStore(),
        scorer,
        GuardrailPolicy(),
        m,
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


def test_first_seen_survives_the_ring_buffer_wrapping():
    """Card age must not be measured against a buffer that has forgotten the card's start.

    `card_age_days` was derived from the history list the caller held. That list is capped
    at 512 events, so a busy card's "oldest" transaction is recent by construction and the
    feature collapsed toward 0 for exactly the cards with the most history.
    """
    store = CardHistoryStore(cap=2)
    for i in range(5):
        store.append("c", {"transaction_id": f"t{i}", "timestamp": f"2026-01-0{i + 1}T00:00:00"})
    assert len(store.get("c")) == 2  # the buffer forgot the early events...
    assert store.first_seen("c") == datetime(2026, 1, 1)  # ...but the card's age did not


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


def test_grounding_is_measured_not_asserted():
    """The guardrail's grounding threshold must be reachable.

    `evaluate_output` was called with `grounding_score=1.0` — a literal. So
    `policy.py`'s `grounding_score < grounding_threshold` branch could never be taken, and
    the GROUNDING policy class was dead code in the only path that runs it, while
    `deploy/local/README.md` advertised the guardrail as real. A control with an
    unreachable branch is a control that does not exist.
    """
    from ml.serving.stream_service import STUB_REFERENCES, estimate_grounding

    grounded = estimate_grounding(
        "This follows from PSD2 Art. 97 (Strong Customer Authentication).", STUB_REFERENCES
    )
    invented = estimate_grounding(
        "This follows from PSD2 Art. 999 and GDPR Art. 5.", STUB_REFERENCES
    )
    silent = estimate_grounding("This transaction looks risky.", STUB_REFERENCES)

    assert grounded == 1.0, "a verdict citing retrieved regulation must score fully grounded"
    assert invented < GuardrailPolicy().grounding_threshold, (
        f"a verdict citing regulation that was never retrieved scored {invented} — at or "
        "above the threshold, so the guardrail would let it through"
    )
    assert silent == 0.0, "reasoning that cites nothing is grounded in nothing"


def test_a_verdict_citing_unretrieved_regulation_is_blocked_by_the_guardrail():
    """End to end: the grounding policy class actually engages on the funnel's own path."""
    from ml.serving.stream_service import STUB_REFERENCES, estimate_grounding

    reasoning = "The score is consistent with a block under PSD2 Art. 999."
    decision = GuardrailPolicy().evaluate_output(
        reasoning, grounding_score=estimate_grounding(reasoning, STUB_REFERENCES)
    )
    assert decision.blocked
    assert decision.policy == "GROUNDING"


# --------------------------------------------------------------------------- #
# A blocked verdict must be blocked — not counted and shipped
# --------------------------------------------------------------------------- #


def _flagged_scored() -> dict:
    return {
        "fraud_score": 0.97,
        "model_version": "fraud-xgb:test",
        "threshold": 0.7,
        "decision_hint": "block",
        "top_features": [{"name": "country_mismatch", "value": True, "contribution": 0.5}],
    }


class _AlwaysBlocks(GuardrailPolicy):
    """A guardrail that finds something. The point is what the funnel DOES about it."""

    def evaluate_output(self, text, *, grounding_score=None):
        from agents.bedrock.guardrails.policy import PII, GuardrailDecision

        return GuardrailDecision(True, PII, "raw PII present in output")


class _AlwaysFlags:
    """A scorer that always flags, so the Tier-2 path is exercised deterministically."""

    class config:  # noqa: N801 - mirrors FraudScorer's attribute shape
        model_version = "fraud-xgb:test"

    def score(self, features):
        return _flagged_scored()


def test_a_guardrail_block_withholds_the_verdict():
    """`if guard.blocked: metrics.record_guardrail_block(...)` was the ENTIRE effect.

    A Prometheus counter went up and the verdict shipped: a verdict the guardrail had just
    identified as leaking a card number was returned to the caller and written verbatim
    into the audit log, with the guardrail's own finding attached as metadata. The control
    detected the leak and passed it on. `blocked` has to mean blocked.
    """
    sink = MemorySink()
    m = ServingMetrics(registry=CollectorRegistry())
    result = process_transaction(
        _txn("t-blocked"),
        CardHistoryStore(),
        _AlwaysFlags(),
        _AlwaysBlocks(),
        m,
        sink,
    )

    assert result["verdict_released"] is False, "the guardrail blocked and the verdict shipped"
    assert result["withheld_because"].startswith("guardrail:")
    assert result["review_queue"], "a withheld verdict must name where it goes"
    assert sink.records[0].released is False


def test_a_refused_decision_record_does_not_take_the_funnel_down(scorer):
    """The PII refusal was a poison pill.

    `record_decision` raises when a record would carry raw PII, and nothing caught it — so
    the trigger condition for this control was a payment-scoring OUTAGE: an unhashed
    card_hash upstream (the exact scenario it exists for) raised out of the consumer loop,
    closed the consumer with the offset uncommitted, and the restart re-read the same
    message. A crash loop, caused by a guardrail. Meanwhile `FeatureComputationError` was
    carefully quarantined per-row eight lines above.
    """
    registry = CollectorRegistry()
    m = ServingMetrics(registry=registry)
    sink = MemorySink()

    # An unhashed PAN where the hash should be — what the control is FOR.
    result = process_transaction(
        _txn("t-pii", card_hash="4111111111111111"),
        CardHistoryStore(),
        scorer,
        GuardrailPolicy(),
        m,
        sink,
    )

    assert result["status"] == "scored", "the funnel died on a refused audit record"
    assert sink.records == [], "raw PII was written to the audit log"
    assert (
        registry.get_sample_value(
            "fintelliguard_decision_log_refusals_total",
            {"endpoint": "fintelliguard-fraud-score", "environment": "local"},
        )
        == 1.0
    ), "the refusal was swallowed silently — an unrecorded decision must page someone"


class _FabricatesRegulation(GuardrailPolicy):
    """Left as the real policy — the point is that the FUNNEL feeds it a real score."""


def test_the_funnel_withholds_an_ungrounded_verdict(monkeypatch):
    """The grounding branch must be reachable THROUGH the funnel, not just in a unit test.

    `test_grounding_is_measured_not_asserted` calls `estimate_grounding` directly with
    hand-written strings — it proves a function. The funnel is a different question, and the
    answer was that `build_stub_verdict` embeds `references[0]` in its own f-string, so the
    grounding it computes on itself is 1.0 for every input and `policy.py`'s threshold branch
    is STILL unreachable in the only path that runs it. The tautology moved up one level; it
    did not die.

    So: make the stub cite regulation that was never retrieved, and require the funnel to
    withhold.
    """
    import ml.serving.stream_service as svc

    original = svc.build_stub_verdict  # capture BEFORE patching, or the stub calls itself

    def _ungrounded(scored, references=svc.STUB_REFERENCES):
        verdict, context = original(scored, references)
        verdict["reasoning"] = "Under PSD2 Article 999 the issuer must decline this transaction."
        return verdict, context

    monkeypatch.setattr(svc, "build_stub_verdict", _ungrounded)

    sink = MemorySink()
    m = ServingMetrics(registry=CollectorRegistry())
    result = svc.process_transaction(
        _txn("t-ungrounded"),
        CardHistoryStore(),
        _AlwaysFlags(),
        _FabricatesRegulation(),
        m,
        sink,
    )

    assert result["verdict_released"] is False, (
        "a verdict citing regulation that was never retrieved reached the caller"
    )
    assert sink.records[0].grounding_score == 0.0
    assert sink.records[0].released is False
