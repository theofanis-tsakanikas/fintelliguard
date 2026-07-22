"""Precise-fact query building blocks over the governed lakehouse tables.

These are the exact-fact primitives behind the `query_lakehouse` tool. In production the
agent reaches them through Genie (NL->SQL over Unity Catalog); here they are plain,
parameterized Spark queries so the precise-fact logic is locally testable. The
transactions DataFrame is injected (a silver/gold table in prod, a sample table in tests).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# pyspark is imported LAZILY (inside the methods), not at module load. The copilot's serving
# artifact and its registration import this package for the OTHER tools (get_fraud_score,
# search_similar_cases), and neither the model-serving container nor the deploy runner ships
# pyspark — a top-level `from pyspark.sql import ...` made merely importing the tools package
# fail with ModuleNotFoundError (copilot registration, deploy run 29892959804). LakehouseTools
# only runs on a Spark cluster (Genie/query_lakehouse), which has pyspark; loading it there,
# when a method is actually called, is correct and keeps the import graph pyspark-free.
if TYPE_CHECKING:
    from pyspark.sql import DataFrame


class LakehouseTools:
    """Parameterized precise-fact queries over a transactions table.

    Expected columns: transaction_id, card_hash, merchant_id, device_id, ip_country,
    amount (double), is_fraud (int, the resolved outcome).
    """

    def __init__(self, transactions: DataFrame) -> None:
        self._txns = transactions

    def merchant_fraud_history(self, merchant_id: str) -> dict[str, Any]:
        """Historical transaction/fraud counts and fraud rate for a merchant."""
        from pyspark.sql import functions as F

        rows = self._txns.filter(F.col("merchant_id") == merchant_id)
        total = rows.count()
        fraud = rows.filter(F.col("is_fraud") == 1).count()
        return {
            "merchant_id": merchant_id,
            "transaction_count": total,
            "fraud_count": fraud,
            "fraud_rate": (fraud / total) if total else 0.0,
        }

    def card_transaction_summary(self, card_hash: str) -> dict[str, Any]:
        """Counts, distinct merchants/devices/countries and total amount for a card."""
        from pyspark.sql import functions as F

        rows = self._txns.filter(F.col("card_hash") == card_hash)
        agg = rows.agg(
            F.count(F.lit(1)).alias("transaction_count"),
            F.countDistinct("merchant_id").alias("distinct_merchants"),
            F.countDistinct("device_id").alias("distinct_devices"),
            F.countDistinct("ip_country").alias("distinct_countries"),
            F.coalesce(F.sum("amount"), F.lit(0.0)).alias("total_amount"),
        ).first()
        return {
            "card_hash": card_hash,
            "transaction_count": int(agg["transaction_count"]),
            "distinct_merchants": int(agg["distinct_merchants"]),
            "distinct_devices": int(agg["distinct_devices"]),
            "distinct_countries": int(agg["distinct_countries"]),
            "total_amount": float(agg["total_amount"]),
        }

    def device_usage(self, device_id: str) -> dict[str, Any]:
        """How many transactions and distinct cards a device has been used by."""
        from pyspark.sql import functions as F

        rows = self._txns.filter(F.col("device_id") == device_id)
        agg = rows.agg(
            F.count(F.lit(1)).alias("transaction_count"),
            F.countDistinct("card_hash").alias("distinct_cards"),
        ).first()
        return {
            "device_id": device_id,
            "transaction_count": int(agg["transaction_count"]),
            "distinct_cards": int(agg["distinct_cards"]),
        }
