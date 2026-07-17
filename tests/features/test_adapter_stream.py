"""Stream adapter window/state correctness and the no-leakage guarantee."""

from __future__ import annotations

from ml.features import validate_feature_vector
from ml.features.adapter_stream import compute_features


def _txn(ts, amount, merchant, device, country="DE", mcc="5411"):
    return {
        "transaction_id": f"t-{ts}",
        "timestamp": ts,
        "amount": amount,
        "merchant_id": merchant,
        "card_hash": "cardA",
        "device_id": device,
        "ip_country": country,
        "mcc_code": mcc,
    }


def test_window_and_state_features():
    history = [
        _txn("2026-01-01T10:00:00+00:00", 100.0, "M1", "D2"),  # >1h before
        _txn("2026-01-01T11:30:00+00:00", 100.0, "M1", "D1"),  # within 1h
        _txn("2026-01-01T11:50:00+00:00", 100.0, "M2", "D1"),  # within 1h
    ]
    current = _txn("2026-01-01T11:55:00+00:00", 100.0, "M3", "D1")

    fv = compute_features(current, history).features
    assert fv.txn_velocity_1h == 3  # two priors within 1h + current
    assert fv.txn_velocity_24h == 4  # three priors within 24h + current
    assert fv.amount_sum_1h == 300.0  # current + two within-1h priors
    assert fv.distinct_merchants_24h == 3  # M1, M2, M3
    assert fv.device_seen_before is True  # D1 used before
    assert fv.device_txn_count_24h == 3  # two prior D1 + current
    assert fv.country_mismatch is False  # all DE
    assert fv.distinct_countries_24h == 1
    assert fv.is_unusual_hour is False  # hour 11 is within seen hours {10, 11}
    validate_feature_vector(fv)


def test_country_mismatch_against_modal():
    history = [
        _txn("2026-01-01T09:00:00+00:00", 20.0, "M1", "D1", country="DE"),
        _txn("2026-01-01T09:30:00+00:00", 20.0, "M1", "D1", country="DE"),
    ]
    current = _txn("2026-01-01T10:00:00+00:00", 20.0, "M1", "D1", country="BR")
    fv = compute_features(current, history).features
    assert fv.country_mismatch is True
    assert fv.distinct_countries_24h == 2


def test_no_target_leakage_future_is_ignored():
    before = _txn("2026-01-01T11:30:00+00:00", 100.0, "M1", "D1")
    future = _txn("2026-01-01T12:30:00+00:00", 999.0, "M2", "D9")  # AFTER current
    current = _txn("2026-01-01T12:00:00+00:00", 100.0, "M3", "D1")

    fv = compute_features(current, [before, future]).features
    # Only the strictly-before txn (+ current) counts; the future txn is invisible.
    assert fv.txn_velocity_1h == 2
    assert fv.txn_velocity_24h == 2
    assert fv.amount_sum_1h == 200.0
    assert fv.distinct_merchants_24h == 2  # M1, M3 (not M2 from the future)
    assert fv.distinct_countries_24h == 1
