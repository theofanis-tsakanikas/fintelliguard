"""The audit trail the AI-Act document has always claimed.

`docs/governance/AI_ACT_ANNEX_IV.md` told a regulator that "every inference is logged
(input -> features -> model -> guardrails -> output) for audit". Nothing implemented it.
The closest thing was a `logger.info` to stdout, for flagged cases only, carrying no
verdict, no gate result, no correlation id and no model version. Record-keeping is AI Act
Art. 12 and it is not optional for a high-risk system.
"""

from __future__ import annotations

import json

import pytest
from prometheus_client import CollectorRegistry

from agents.bedrock.eval.decision_log import (
    DecisionLogError,
    DecisionRecord,
    JsonlSink,
    MemorySink,
    new_decision_id,
    record_decision,
    utc_now,
)
from agents.bedrock.guardrails.policy import GuardrailPolicy
from ml.features.schema import FEATURE_NAMES
from ml.serving.local_model import train_demo_scorer
from ml.serving.metrics import ServingMetrics
from ml.serving.stream_service import CardHistoryStore, process_transaction


@pytest.fixture(scope="module")
def demo():
    return train_demo_scorer(max_records=400, seed=7)


def _txn(txn_id: str = "t1", **over: object) -> dict:
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


def _record(**over: object) -> DecisionRecord:
    base = {
        "decision_id": new_decision_id(),
        "transaction_id": "t1",
        "card_hash": "b8c1f0",
        "recorded_at": utc_now(),
        "model_version": "fraud-xgb:7",
        "features": {"amount_usd": 50.0},
        "fraud_score": 0.42,
        "decision_hint": "allow",
        "top_features": ("amount_usd",),
    }
    base.update(over)
    return DecisionRecord(**base)  # type: ignore[arg-type]


def test_every_scored_transaction_is_recorded(demo):
    """Not just the flagged ones — an unrecorded decision cannot be audited."""
    sink = MemorySink()
    process_transaction(
        _txn(),
        CardHistoryStore(),
        demo.scorer,
        GuardrailPolicy(),
        ServingMetrics(registry=CollectorRegistry()),
        demo.merchant_risk_table,
        sink,
    )
    assert len(sink.records) == 1


def test_the_record_carries_the_model_version_that_made_the_decision(demo):
    """The field every consumer dropped on the floor.

    The scorer's contract has always carried `model_version`, and nothing kept it. So
    "which model decided this transaction, and what did its card say?" had no answer for
    any decision the system had ever made — and the generated model card documented a model
    that could not be tied to a single one of its own outputs.
    """
    sink = MemorySink()
    process_transaction(
        _txn(),
        CardHistoryStore(),
        demo.scorer,
        GuardrailPolicy(),
        ServingMetrics(registry=CollectorRegistry()),
        demo.merchant_risk_table,
        sink,
    )
    record = sink.records[0]
    assert record.model_version == demo.scorer.config.model_version
    assert record.model_version  # not empty


def test_the_record_is_replayable(demo):
    """input -> features -> model -> guardrails -> output, exactly as the doc describes."""
    sink = MemorySink()
    process_transaction(
        _txn(),
        CardHistoryStore(),
        demo.scorer,
        GuardrailPolicy(),
        ServingMetrics(registry=CollectorRegistry()),
        demo.merchant_risk_table,
        sink,
    )
    record = sink.records[0]
    assert record.transaction_id == "t1"
    assert set(record.features) == set(FEATURE_NAMES), "the record must carry every feature"
    assert 0.0 <= record.fraud_score <= 1.0
    assert record.top_features
    assert record.decision_id and record.recorded_at


def test_a_flagged_transaction_records_the_verdict_and_both_gate_outcomes(demo):
    """A Tier-2 decision without its gate result is not an audit record."""
    sink = MemorySink()
    scored = {
        "fraud_score": 0.97,
        "model_version": "v",
        "threshold": 0.7,
        "decision_hint": "block",
        "top_features": [{"name": "country_mismatch", "value": True, "contribution": 0.5}],
    }
    from ml.serving.stream_service import build_stub_verdict, estimate_grounding

    verdict, _ctx = build_stub_verdict(scored)
    record = _record(
        verdict=verdict,
        gate_accepted=True,
        gate_failures=(),
        guardrail_blocked=False,
        grounding_score=estimate_grounding(verdict["reasoning"], ("PSD2 Art. 97 (SCA)",)),
    )
    record_decision(sink, record)
    stored = sink.records[0]
    assert stored.verdict is not None
    assert stored.gate_accepted is True
    assert stored.guardrail_blocked is False


def test_the_log_refuses_to_write_raw_pii():
    """An audit log of a fraud system is a high-value target.

    Refused rather than redacted: silently rewriting evidence is worse than declining to
    record it, because the redacted record still looks complete.
    """
    sink = MemorySink()
    with pytest.raises(DecisionLogError, match="raw PII"):
        record_decision(sink, _record(verdict={"reasoning": "Cardholder 4111 1111 1111 1111."}))
    assert sink.records == []  # nothing was written


def test_a_computed_feature_is_not_mistaken_for_a_card_number():
    """`amount_log = 3.9318256327243257` is not PII.

    The card-number pattern was `\\b(?:\\d[ -]?){13,19}\\b`, which matches the mantissa of a
    float: sixteen consecutive digits, with `\\b` sitting happily after the decimal point.
    Every record carrying a computed feature would have been refused, and every verdict
    quoting one blocked by the guardrail — whose false-positive rate is a published number.
    Found by feeding the real feature vector through the real log.
    """
    sink = MemorySink()
    record_decision(sink, _record(features={"amount_log": 3.9318256327243257}))
    assert len(sink.records) == 1


def test_the_jsonl_sink_appends_and_round_trips(tmp_path):
    """Append-only: a record that can be overwritten in place is not evidence."""
    path = tmp_path / "decisions.jsonl"
    sink = JsonlSink(path)
    record_decision(sink, _record(transaction_id="a"))
    record_decision(sink, _record(transaction_id="b"))

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # appended, not replaced
    assert [json.loads(line)["transaction_id"] for line in lines] == ["a", "b"]
    assert json.loads(lines[0])["model_version"] == "fraud-xgb:7"
