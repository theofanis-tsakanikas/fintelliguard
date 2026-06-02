"""Precise-fact lakehouse queries on a small sample table (local Spark)."""

from __future__ import annotations

from agents.databricks.tools.query_lakehouse import LakehouseTools


def test_merchant_fraud_history(transactions):
    tools = LakehouseTools(transactions)
    result = tools.merchant_fraud_history("M1")
    assert result == {
        "merchant_id": "M1",
        "transaction_count": 3,  # t1, t2, t3
        "fraud_count": 1,  # t2
        "fraud_rate": 1 / 3,
    }


def test_merchant_with_no_history_has_zero_rate(transactions):
    tools = LakehouseTools(transactions)
    result = tools.merchant_fraud_history("DOES_NOT_EXIST")
    assert result["transaction_count"] == 0
    assert result["fraud_rate"] == 0.0


def test_card_transaction_summary(transactions):
    tools = LakehouseTools(transactions)
    result = tools.card_transaction_summary("C1")
    assert result == {
        "card_hash": "C1",
        "transaction_count": 4,  # t1, t2, t4, t6
        "distinct_merchants": 3,  # M1, M2, M3
        "distinct_devices": 2,  # D1, D2
        "distinct_countries": 2,  # DE, FR
        "total_amount": 180.0,  # 100 + 50 + 20 + 10
    }


def test_device_usage(transactions):
    tools = LakehouseTools(transactions)
    result = tools.device_usage("D1")
    assert result == {
        "device_id": "D1",
        "transaction_count": 4,  # t1, t3, t4, t6
        "distinct_cards": 2,  # C1, C2
    }
