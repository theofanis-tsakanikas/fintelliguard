"""Pure Spark transforms for the silver layer: cleanse, type, enrich, validate.

Validation rules are expressed as SQL gates so the SAME conditions drive both the
quarantine split (here) and the DLT `@dlt.expect_all` metrics (in silver_pipeline.py).
Failing rows are routed to a quarantine frame, never silently dropped.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from ml.features.adapter_stream import MCC_RISK_TIERS
from pipelines.common import add_quarantine_reason, select_quarantined, select_valid

__all__ = [
    "SILVER_IEEE_GATES",
    "SILVER_TXN_GATES",
    "cleanse_ieee",
    "cleanse_transactions",
    "select_quarantined",
    "select_valid",
]

# --- stream transactions -----------------------------------------------------

# Gates a clean transaction must satisfy. Each is a boolean SQL expression; a null
# result counts as a failure (handled in add_quarantine_reason).
SILVER_TXN_GATES: dict[str, str] = {
    "amount_positive_bounded": "amount IS NOT NULL AND amount > 0 AND amount < 1000000",
    "valid_card_hash": "card_hash IS NOT NULL AND card_hash RLIKE '^[0-9a-f]{32}$'",
    "valid_event_time": "event_time IS NOT NULL",
    "valid_country": "ip_country IS NOT NULL AND ip_country RLIKE '^[A-Z]{2}$'",
    "required_not_null": (
        "transaction_id IS NOT NULL AND merchant_id IS NOT NULL AND device_id IS NOT NULL"
    ),
}


def _mcc_tier_column(mcc_col: str) -> Column:
    """Map mcc_code -> risk tier via the shared ml/features table (default 2)."""
    expr = F.lit(2)
    for code, tier in MCC_RISK_TIERS.items():
        expr = F.when(F.col(mcc_col) == F.lit(code), F.lit(tier)).otherwise(expr)
    return expr.cast("int")


def cleanse_transactions(bronze: DataFrame) -> DataFrame:
    """Type, normalize and enrich bronze stream rows, then tag quarantine reasons.

    Keeps the original ISO `timestamp` string (gold's adapter parses it) alongside a
    typed `event_time`. Normalizes card_hash to lower-case hex and ip_country to ISO
    upper-case, and enriches with `mcc_risk_tier`.
    """
    cleaned = bronze.select(
        F.col("transaction_id"),
        F.col("timestamp"),
        F.to_timestamp("timestamp").alias("event_time"),
        F.col("amount").cast("double").alias("amount"),
        F.col("merchant_id"),
        F.lower(F.col("card_hash")).alias("card_hash"),
        F.col("device_id"),
        F.upper(F.col("ip_country")).alias("ip_country"),
        F.col("mcc_code"),
        _mcc_tier_column("mcc_code").alias("mcc_risk_tier"),
    )
    return add_quarantine_reason(cleaned, SILVER_TXN_GATES)


# --- IEEE-CIS batch ----------------------------------------------------------

SILVER_IEEE_GATES: dict[str, str] = {
    "amount_positive": "TransactionAmt IS NOT NULL AND TransactionAmt > 0",
    "has_card": "card1 IS NOT NULL",
    "has_label": "isFraud IS NOT NULL",
}


def cleanse_ieee(bronze: DataFrame) -> DataFrame:
    """Type and impute the IEEE-CIS columns the adapter uses; tag quarantine reasons.

    Proxy count/time columns (C/D/dist) are imputed to 0 when missing. The anonymized
    V1-V339 columns are intentionally NOT carried — they break feature parity.
    """
    cleaned = bronze.select(
        F.col("TransactionID").cast("string").alias("TransactionID"),
        F.col("card1"),
        F.col("TransactionAmt").cast("double").alias("TransactionAmt"),
        F.coalesce(F.col("C1").cast("double"), F.lit(0.0)).alias("C1"),
        F.coalesce(F.col("C2").cast("double"), F.lit(0.0)).alias("C2"),
        F.coalesce(F.col("C4").cast("double"), F.lit(0.0)).alias("C4"),
        F.coalesce(F.col("C6").cast("double"), F.lit(0.0)).alias("C6"),
        F.coalesce(F.col("D1").cast("double"), F.lit(0.0)).alias("D1"),
        # `nanvl`, not a bare cast: every other numeric column here is guarded and this one
        # was not. A NaN addr2 is not NULL, so it survived to the adapter, where
        # `nan != modal_addr2` is True and `country_mismatch` came out spuriously True on
        # ~12% of real IEEE-CIS rows — one of the strongest features in the label.
        # `-1` is outside the ISO country-code range, so it reads as "unknown", and the
        # adapter's modal comparison treats it as a value like any other.
        F.nanvl(F.col("addr2").cast("double"), F.lit(-1.0)).alias("addr2"),
        F.coalesce(F.col("dist1").cast("double"), F.lit(0.0)).alias("dist1"),
        F.col("ProductCD"),
        F.col("TransactionDT").cast("double").alias("TransactionDT"),
        F.col("isFraud").cast("int").alias("isFraud"),
    )
    return add_quarantine_reason(cleaned, SILVER_IEEE_GATES)
