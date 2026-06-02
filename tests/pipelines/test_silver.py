"""Silver transforms: cleansing/enrichment rules and the quarantine routing path."""

from __future__ import annotations

from pipelines.common import select_quarantined, select_valid
from pipelines.silver.silver_transforms import cleanse_ieee, cleanse_transactions

_TXN_SCHEMA = (
    "transaction_id string, timestamp string, amount double, merchant_id string, "
    "card_hash string, device_id string, ip_country string, mcc_code string"
)


def test_cleanse_transactions_valid_enrich_and_normalize(spark):
    rows = [
        ("t1", "2026-01-01T12:00:00+00:00", 50.0, "M1", "0" * 32, "D1", "de", "7995"),
        ("t2", "2026-01-01T12:00:00+00:00", -5.0, "M2", "0" * 32, "D1", "DE", "5411"),
        ("t3", "2026-01-01T12:00:00+00:00", 20.0, "M3", "not-hex", "D1", "DE", "5411"),
    ]
    cleaned = cleanse_transactions(spark.createDataFrame(rows, _TXN_SCHEMA))

    valid = select_valid(cleaned).collect()
    assert len(valid) == 1
    row = valid[0]
    assert row["transaction_id"] == "t1"
    assert row["ip_country"] == "DE"  # ISO upper-cased
    assert row["card_hash"] == "0" * 32  # lower-cased hex
    assert row["mcc_risk_tier"] == 5  # 7995 -> tier 5 (shared ml/features table)

    quarantined = {
        r["transaction_id"]: r["_quarantine_reason"] for r in select_quarantined(cleaned).collect()
    }
    assert quarantined == {"t2": "amount_positive_bounded", "t3": "valid_card_hash"}


_IEEE_SCHEMA = (
    "TransactionID int, card1 int, TransactionAmt double, C1 double, C2 double, "
    "C4 double, C6 double, D1 double, addr2 double, dist1 double, ProductCD string, "
    "TransactionDT double, isFraud int"
)


def test_cleanse_ieee_valid_and_quarantine(spark):
    rows = [
        (1, 1000, 100.0, 2.0, 5.0, 3.0, 1.0, 30.0, 87.0, 0.0, "W", 43200.0, 0),
        (2, 1001, -1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 87.0, 0.0, "C", 10.0, 1),
        (3, 1002, 75.0, 1.0, 1.0, 1.0, 1.0, 0.0, 60.0, 0.0, "W", 10.0, None),
    ]
    cleaned = cleanse_ieee(spark.createDataFrame(rows, _IEEE_SCHEMA))

    valid = select_valid(cleaned).collect()
    assert [r["TransactionID"] for r in valid] == ["1"]

    quarantined = {
        r["TransactionID"]: r["_quarantine_reason"] for r in select_quarantined(cleaned).collect()
    }
    assert quarantined == {"2": "amount_positive", "3": "has_label"}


def test_cleanse_ieee_imputes_null_proxy_columns(spark):
    rows = [(9, 2000, 80.0, None, None, None, None, None, 50.0, None, "W", 100.0, 0)]
    cleaned = cleanse_ieee(spark.createDataFrame(rows, _IEEE_SCHEMA))
    row = select_valid(cleaned).collect()[0]
    assert row["C1"] == 0.0 and row["D1"] == 0.0 and row["dist1"] == 0.0  # imputed
