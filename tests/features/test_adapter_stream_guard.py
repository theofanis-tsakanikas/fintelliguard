"""The stream adapter must not crash a whole card group on one malformed timestamp.

A bad CURRENT timestamp raises a typed, quarantinable error; a bad HISTORY timestamp is
skipped so the group still computes. The simulator always emits valid ISO, but rescued /
real Kafka rows will not — this is the defence-in-depth the raw `datetime.fromisoformat`
lacked.
"""

from __future__ import annotations

import pytest

from ml.features.adapter_stream import FeatureComputationError, compute_features


def _txn(txn_id: str, ts: str, amount: float = 100.0, **over: object) -> dict:
    base = {
        "transaction_id": txn_id,
        "timestamp": ts,
        "amount": amount,
        "merchant_id": "m1",
        "card_hash": "cardA",
        "device_id": "dev1",
        "ip_country": "US",
        "mcc_code": "5411",
    }
    base.update(over)
    return base


def test_bad_current_timestamp_raises_typed_error():
    with pytest.raises(FeatureComputationError, match="invalid ISO-8601 timestamp"):
        compute_features(_txn("t1", "not-a-timestamp"), [])


def test_missing_current_timestamp_raises_typed_error():
    bad = _txn("t1", "2026-01-01T00:00:00")
    del bad["timestamp"]
    with pytest.raises(FeatureComputationError):
        compute_features(bad, [])


def test_bad_history_row_is_skipped_not_fatal():
    current = _txn("t3", "2026-01-01T12:00:00")
    history = [
        _txn("h1", "2026-01-01T11:00:00"),  # valid — counts
        _txn("h2", "GARBAGE"),  # malformed — must be skipped, not crash
        _txn("h3", None),  # missing — skipped
    ]
    rec = compute_features(current, history)
    # Only the one valid prior event is counted (plus the current txn itself).
    assert rec.features.txn_velocity_24h == 2
    assert rec.transaction_id == "t3"


def test_valid_input_unchanged_by_the_guard():
    current = _txn("t2", "2026-01-01T12:00:00", amount=200.0)
    history = [_txn("h1", "2026-01-01T11:30:00", amount=100.0)]
    rec = compute_features(current, history)
    assert rec.features.txn_velocity_1h == 2  # one prior within the hour + current
    assert rec.features.amount_usd == 200.0
