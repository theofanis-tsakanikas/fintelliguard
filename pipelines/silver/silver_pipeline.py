"""Thin DLT layer for silver — runs only on Databricks.

Clean rows land in `silver.*_clean`; rows failing a gate land in `silver.*_quarantine`
(routed, not dropped). The `@dlt.expect_all` gates mirror the quarantine SQL gates so the
same rules surface as DLT data-quality metrics. Import/lint-validated locally.
"""

from __future__ import annotations

import dlt
from pyspark.sql import DataFrame

from . import silver_transforms


@dlt.table(
    name="silver.transactions_clean",
    comment="Cleansed, typed, enriched stream transactions (validated).",
)
@dlt.expect_all(silver_transforms.SILVER_TXN_GATES)
def transactions_clean() -> DataFrame:
    cleaned = silver_transforms.cleanse_transactions(dlt.read_stream("bronze.transactions_stream"))
    return silver_transforms.select_valid(cleaned)


@dlt.table(
    name="silver.transactions_quarantine",
    comment="Stream transactions that failed a silver gate, kept for inspection.",
)
def transactions_quarantine() -> DataFrame:
    cleaned = silver_transforms.cleanse_transactions(dlt.read_stream("bronze.transactions_stream"))
    return silver_transforms.select_quarantined(cleaned)


@dlt.table(
    name="silver.ieee_cis_clean",
    comment="Typed, imputed IEEE-CIS rows for training (validated).",
)
@dlt.expect_all(silver_transforms.SILVER_IEEE_GATES)
def ieee_cis_clean() -> DataFrame:
    cleaned = silver_transforms.cleanse_ieee(dlt.read("bronze.ieee_cis_raw"))
    return silver_transforms.select_valid(cleaned)


@dlt.table(
    name="silver.ieee_cis_quarantine",
    comment="IEEE-CIS rows that failed a silver gate, kept for inspection.",
)
def ieee_cis_quarantine() -> DataFrame:
    cleaned = silver_transforms.cleanse_ieee(dlt.read("bronze.ieee_cis_raw"))
    return silver_transforms.select_quarantined(cleaned)
