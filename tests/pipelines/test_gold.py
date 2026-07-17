"""Gold transforms: feature schema, end-to-end parity with ml/features, quarantine."""

from __future__ import annotations

from datetime import datetime

import pytest

from ml.features.adapter_stream import compute_features
from ml.features.merchant_risk import build_merchant_risk_table
from ml.features.schema import FEATURE_NAMES, LOOKUP_KEY, PRIMARY_KEY
from pipelines.common import feature_record_schema, select_quarantined, select_valid
from pipelines.gold.gold_transforms import (
    build_realtime_features,
    build_training_features,
    gate_features,
)

_STREAM_SCHEMA = (
    "transaction_id string, timestamp string, amount double, merchant_id string, "
    "card_hash string, device_id string, ip_country string, mcc_code string"
)

_STREAM_ROWS = [
    ("a1", "2026-01-01T11:00:00+00:00", 100.0, "M1", "cardA", "D1", "DE", "5411"),
    ("a2", "2026-01-01T11:30:00+00:00", 200.0, "M2", "cardA", "D1", "DE", "7995"),
    ("a3", "2026-01-01T11:45:00+00:00", 100.0, "M1", "cardA", "D2", "FR", "6011"),
    ("b1", "2026-01-01T09:00:00+00:00", 30.0, "M3", "cardB", "D9", "US", "5812"),
    ("b2", "2026-01-01T20:00:00+00:00", 40.0, "M3", "cardB", "D9", "US", "5812"),
]
_KEYS = (
    "transaction_id",
    "timestamp",
    "amount",
    "merchant_id",
    "card_hash",
    "device_id",
    "ip_country",
    "mcc_code",
)


_RISK_TABLE = build_merchant_risk_table(
    [{"merchant_id": "M1", "is_fraud": i % 10 == 0} for i in range(200)]
    + [{"merchant_id": "M2", "is_fraud": i % 3 == 0} for i in range(200)]
    + [{"merchant_id": "M3", "is_fraud": False} for _ in range(200)]
)


def _expected_realtime():
    """Pure-python expected features, replaying each card's history in order.

    This mirrors the implementation, so it can only catch plumbing errors in the
    `applyInPandas` wiring — never a bug inside the adapter. That is a fair thing for THIS
    test to do, because the adapter's semantics are proven elsewhere by independent paths
    (`tests/features/test_parity_distributional.py`). Naming the boundary matters: the
    training-side version of this mirror was silently the only parity check that existed,
    and it encoded a leakage bug as the expected answer.
    """
    by_card: dict[str, list[dict]] = {}
    for row in _STREAM_ROWS:
        rec = dict(zip(_KEYS, row, strict=True))
        by_card.setdefault(rec["card_hash"], []).append(rec)

    expected = {}
    for rows in by_card.values():
        rows.sort(key=lambda r: (r["timestamp"], r["transaction_id"]))
        first_seen = datetime.fromisoformat(rows[0]["timestamp"])
        history: list[dict] = []
        for rec in rows:
            expected[rec["transaction_id"]] = compute_features(
                rec,
                history,
                merchant_risk_table=_RISK_TABLE,
                card_first_seen=first_seen,
            ).features.as_dict()
            history.append(rec)
    return expected


def test_gold_realtime_schema_and_parity(spark):
    silver = spark.createDataFrame(_STREAM_ROWS, _STREAM_SCHEMA)
    gold = {
        r["transaction_id"]: r.asDict()
        for r in build_realtime_features(silver, merchant_risk_table=_RISK_TABLE).collect()
    }

    # Exactly the canonical schema: keys + the 15 features.
    sample = next(iter(gold.values()))
    assert tuple(sample.keys()) == (PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES)

    expected = _expected_realtime()
    assert set(gold) == set(expected)
    for txn_id, feats in expected.items():
        produced = gold[txn_id]
        for name in FEATURE_NAMES:
            if isinstance(feats[name], float):
                assert produced[name] == pytest.approx(feats[name]), f"{txn_id}.{name}"
            else:
                assert produced[name] == feats[name], f"{txn_id}.{name}"


def _feature_row(transaction_id, **overrides):
    base = {
        PRIMARY_KEY: transaction_id,
        LOOKUP_KEY: "card",
        "amount_usd": 50.0,
        "amount_log": 3.93,
        "amount_zscore": 0.0,
        "txn_velocity_1h": 1,
        "txn_velocity_24h": 1,
        "amount_sum_1h": 50.0,
        "distinct_merchants_24h": 1,
        "card_age_days": 0,
        "device_seen_before": False,
        "device_txn_count_24h": 1,
        "country_mismatch": False,
        "distinct_countries_24h": 1,
        "merchant_risk_score": 0.0,
        "mcc_risk_tier": 2,
        "is_unusual_hour": False,
    }
    base.update(overrides)
    return base


def test_gold_gate_routes_bad_feature_to_quarantine(spark):
    df = spark.createDataFrame(
        [_feature_row("good"), _feature_row("bad", mcc_risk_tier=9)],
        schema=feature_record_schema(),
    )
    gated = gate_features(df)

    valid = select_valid(gated).collect()
    quarantined = select_quarantined(gated).collect()
    assert [r[PRIMARY_KEY] for r in valid] == ["good"]
    assert len(quarantined) == 1
    assert quarantined[0][PRIMARY_KEY] == "bad"
    assert quarantined[0]["_quarantine_reason"] == "mcc_tier_valid"


_IEEE_SCHEMA = (
    "TransactionID int, card1 int, TransactionAmt double, C1 double, C2 double, "
    "C4 double, C6 double, D1 double, addr2 double, dist1 double, ProductCD string, "
    "TransactionDT double, isFraud int"
)
_IEEE_ROWS = [
    (1, 1000, 100.0, 2.0, 5.0, 3.0, 1.0, 30.0, 87.0, 0.0, "W", 43200.0, 0),
    (2, 1000, 300.0, 4.0, 9.0, 2.0, 1.0, 30.0, 87.0, 50.0, "C", 10.0, 1),
    (3, 1000, 50.0, 1.0, 1.0, 1.0, 1.0, 30.0, 60.0, 0.0, "W", 43200.0, 0),
]
_IEEE_KEYS = (
    "TransactionID",
    "card1",
    "TransactionAmt",
    "C1",
    "C2",
    "C4",
    "C6",
    "D1",
    "addr2",
    "dist1",
    "ProductCD",
    "TransactionDT",
    "isFraud",
)


def test_gold_training_schema_and_label(spark):
    silver = spark.createDataFrame(_IEEE_ROWS, _IEEE_SCHEMA)
    gold = {int(r[PRIMARY_KEY]): r.asDict() for r in build_training_features(silver).collect()}

    sample = next(iter(gold.values()))
    assert tuple(sample.keys()) == (PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES, "is_fraud")

    for row in _IEEE_ROWS:
        rec = dict(zip(_IEEE_KEYS, row, strict=True))
        assert gold[rec["TransactionID"]]["is_fraud"] == rec["isFraud"]


def test_gold_training_features_do_not_see_the_future(spark):
    """A transaction's training features must not change when LATER ones are added.

    This replaces a test that computed its expectation like this:

        amounts = [r[2] for r in _IEEE_ROWS]          # every row of the card...
        ctx = CardContext(amount_mean=statistics.mean(amounts), ...)   # ...including the future
        expected = map_row(rec, ctx)

    which is exactly what the implementation did, so the two agreed and the test passed —
    while `amount_zscore` and `country_mismatch` were computed at training against
    statistics containing the row being scored and everything after it. The test mirrored
    the leakage instead of catching it. There is no way to write that mirror and have it
    fail.

    Feeding the same prefix twice — once alone, once followed by more transactions — asks
    the only question that matters: does the past depend on the future?
    """
    prefix = _IEEE_ROWS[:2]
    prefix_only = spark.createDataFrame(prefix, _IEEE_SCHEMA)
    with_future = spark.createDataFrame(_IEEE_ROWS, _IEEE_SCHEMA)

    before = {
        int(r[PRIMARY_KEY]): r.asDict() for r in build_training_features(prefix_only).collect()
    }
    after = {
        int(r[PRIMARY_KEY]): r.asDict() for r in build_training_features(with_future).collect()
    }

    for row in prefix:
        txn_id = row[0]
        for name in FEATURE_NAMES:
            assert before[txn_id][name] == pytest.approx(after[txn_id][name]), (
                f"{name} on transaction {txn_id} changed when a LATER transaction was "
                "added to the card — the training features are computed from the future"
            )
