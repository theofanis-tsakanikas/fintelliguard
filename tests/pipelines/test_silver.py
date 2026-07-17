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


def test_cleanse_ieee_maps_missing_addr2_to_null_not_a_sentinel(spark):
    """Missing addr2 (NULL or NaN) must survive Silver as NULL, never a `-1.0` sentinel.

    A sentinel is a *present* country code: `_usable` downstream would not filter it, so on a
    card mixing real and missing addr2 the missing rows would compare unequal to the card's
    modal country and produce a spurious `country_mismatch = True`. NULL is the one honest
    "we do not know the billing country" value the adapter and Gold both collapse to False.
    """
    rows = [
        (10, 3000, 80.0, 1.0, 1.0, 1.0, 1.0, 0.0, None, 0.0, "W", 100.0, 0),
        (11, 3000, 80.0, 1.0, 1.0, 1.0, 1.0, 0.0, float("nan"), 0.0, "W", 200.0, 0),
        (12, 3000, 80.0, 1.0, 1.0, 1.0, 1.0, 0.0, 87.0, 0.0, "W", 300.0, 0),
    ]
    cleaned = cleanse_ieee(spark.createDataFrame(rows, _IEEE_SCHEMA))
    addr2 = {r["TransactionID"]: r["addr2"] for r in select_valid(cleaned).collect()}
    assert addr2["10"] is None  # source NULL stays NULL
    assert addr2["11"] is None  # NaN normalised to NULL, not -1.0
    assert addr2["12"] == 87.0  # a real code is untouched


def test_missing_addr2_does_not_flag_country_mismatch_end_to_end(spark):
    """The Silver->Gold composition: a missing addr2 row on a mixed card is not a mismatch.

    This is the regression the two independent fixes (Silver's NaN guard, the adapter's
    `_usable`) once defeated each other on — the `-1.0` sentinel made `country_mismatch`
    spuriously True here. Drive real Silver output through the real `_training_group`.
    """
    from pipelines.gold.gold_transforms import _training_group

    rows = [
        (20, 4000, 80.0, 1.0, 1.0, 1.0, 1.0, 0.0, 87.0, 0.0, "W", 100.0, 0),  # real addr2
        (21, 4000, 80.0, 1.0, 1.0, 1.0, 1.0, 0.0, float("nan"), 0.0, "W", 200.0, 0),  # missing
        (22, 4000, 80.0, 1.0, 1.0, 1.0, 1.0, 0.0, 87.0, 0.0, "W", 300.0, 0),  # real addr2
    ]
    cleaned = select_valid(cleanse_ieee(spark.createDataFrame(rows, _IEEE_SCHEMA)))
    pdf = cleaned.toPandas().sort_values("TransactionDT")

    produced = _training_group(pdf).set_index("transaction_id")
    # The missing-addr2 row must NOT be flagged as a country mismatch: you cannot mismatch a
    # country you do not know.
    assert bool(produced.loc["21", "country_mismatch"]) is False
