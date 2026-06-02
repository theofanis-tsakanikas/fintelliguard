"""IEEE-CIS proxy mappings on small synthetic, hand-built rows (no real dataset)."""

from __future__ import annotations

import math

from ml.features import validate_feature_vector
from ml.features.adapter_ieee import CardContext, map_row


def test_proxy_mapping_on_known_row():
    row = {
        "TransactionID": 1001,
        "card1": 1234,
        "TransactionAmt": 120.0,
        "C1": 3,  # -> txn_velocity_1h
        "C2": 10,  # -> txn_velocity_24h
        "C4": 4,  # -> distinct_merchants_24h
        "C6": 2,  # -> device_txn_count_24h
        "D1": 45,  # -> card_age_days
        "addr2": 87.0,  # -> country (vs modal)
        "dist1": 50.0,  # -> distinct_countries proxy
        "ProductCD": "C",  # -> merchant risk / tier
        "TransactionDT": 3 * 3600 + 5,  # -> hour 3 (night)
    }
    ctx = CardContext(amount_mean=100.0, amount_std=20.0, modal_addr2=60.0)
    record = map_row(row, ctx)
    fv = record.features

    assert record.transaction_id == "1001"
    assert record.card_hash == "1234"
    assert fv.amount_usd == 120.0
    assert fv.amount_log == math.log1p(120.0)
    assert fv.amount_zscore == (120.0 - 100.0) / 20.0
    assert fv.txn_velocity_1h == 3
    assert fv.txn_velocity_24h == 10
    assert fv.amount_sum_1h == 3 * 120.0
    assert fv.distinct_merchants_24h == 4
    assert fv.card_age_days == 45
    assert fv.device_seen_before is True
    assert fv.device_txn_count_24h == 2
    assert fv.country_mismatch is True  # addr2 87 != modal 60
    assert fv.distinct_countries_24h == 2  # dist1 > 0
    assert fv.merchant_risk_score == 0.12  # ProductCD C
    assert fv.mcc_risk_tier == 5  # ProductCD C
    assert fv.is_unusual_hour is True  # hour 3
    validate_feature_vector(fv)


def test_velocity_monotonicity_is_enforced_when_proxy_inverts():
    # C1 > C2 in the raw data must not violate the 24h >= 1h gate.
    row = {
        "TransactionID": 2,
        "card1": 9,
        "TransactionAmt": 10.0,
        "C1": 10,
        "C2": 3,
        "TransactionDT": 12 * 3600,
        "ProductCD": "W",
    }
    fv = map_row(row).features
    assert fv.txn_velocity_1h == 10
    assert fv.txn_velocity_24h == 10  # max(C2, C1)
    assert fv.txn_velocity_24h >= fv.txn_velocity_1h
    validate_feature_vector(fv)


def test_neutral_defaults_without_context_or_optional_columns():
    row = {
        "TransactionID": 3,
        "card1": 7,
        "TransactionAmt": 25.0,
        "D1": 0,  # never seen
        "dist1": 0.0,  # single location
        "ProductCD": "Z",  # unknown -> defaults
        "TransactionDT": 12 * 3600,  # midday
    }
    fv = map_row(row).features
    assert fv.amount_zscore == 0.0  # no context stats
    assert fv.device_seen_before is False  # D1 == 0
    assert fv.country_mismatch is False  # no modal addr2 / no addr2
    assert fv.distinct_countries_24h == 1  # dist1 == 0
    assert fv.merchant_risk_score == 0.02  # default
    assert fv.mcc_risk_tier == 2  # default
    assert fv.is_unusual_hour is False  # midday
    validate_feature_vector(fv)
