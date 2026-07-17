"""Thin DLT layer for silver — runs only on Databricks.

Clean rows land in `silver.*_clean`; rows failing a gate land in `silver.*_quarantine`
(routed, not dropped). Import/lint-validated locally.

**Where the expectations live, and why it matters.** This used to be the shape:

    @dlt.expect_all(silver_transforms.SILVER_TXN_GATES)
    def transactions_clean() -> DataFrame:
        cleaned = silver_transforms.cleanse_transactions(dlt.read_stream(...))
        return silver_transforms.select_valid(cleaned)   # <- failures already removed

`select_valid` drops every row that fails a gate BEFORE the frame reaches DLT. The
decorator then evaluated those same gates against a frame from which all violations had
been removed, so the data-quality dashboard reported 100% pass — permanently, by
construction, including during a total upstream corruption event. A green metric that
cannot turn red is worse than no metric: it is evidence of a check that never happened.

Doing both was the error. The gates are now measured on a view of the CLEANSED, UNFILTERED
frame, where a failing row still exists and the percentage can move; the clean and
quarantine tables then split that view. `expect_all` (not `_or_drop`) is deliberate: this
project routes bad rows to quarantine rather than dropping them, so the expectation is
there to MEASURE, and the split is what enforces.

It also stops `cleanse_*` being computed twice per update, once per table.
"""

from __future__ import annotations

import dlt
from pyspark.sql import DataFrame

from . import silver_transforms


@dlt.view(
    name="silver.transactions_gated",
    comment="Cleansed stream transactions, tagged pass/fail. The DQ metric is measured here.",
)
@dlt.expect_all(silver_transforms.SILVER_TXN_GATES)
def transactions_gated() -> DataFrame:
    # Unfiltered on purpose: a row that fails a gate must still BE here, or the expectation
    # below has nothing to fail on.
    return silver_transforms.cleanse_transactions(dlt.read_stream("bronze.transactions_stream"))


@dlt.table(
    name="silver.transactions_clean",
    comment="Cleansed, typed, enriched stream transactions (validated).",
)
def transactions_clean() -> DataFrame:
    return silver_transforms.select_valid(dlt.read_stream("silver.transactions_gated"))


@dlt.table(
    name="silver.transactions_quarantine",
    comment="Stream transactions that failed a silver gate, kept for inspection.",
)
def transactions_quarantine() -> DataFrame:
    return silver_transforms.select_quarantined(dlt.read_stream("silver.transactions_gated"))


@dlt.view(
    name="silver.ieee_cis_gated",
    comment="Typed IEEE-CIS rows, tagged pass/fail. The DQ metric is measured here.",
)
@dlt.expect_all(silver_transforms.SILVER_IEEE_GATES)
def ieee_cis_gated() -> DataFrame:
    return silver_transforms.cleanse_ieee(dlt.read("bronze.ieee_cis_raw"))


@dlt.table(
    name="silver.ieee_cis_clean",
    comment="Typed, imputed IEEE-CIS rows for training (validated).",
)
def ieee_cis_clean() -> DataFrame:
    return silver_transforms.select_valid(dlt.read("silver.ieee_cis_gated"))


@dlt.table(
    name="silver.ieee_cis_quarantine",
    comment="IEEE-CIS rows that failed a silver gate, kept for inspection.",
)
def ieee_cis_quarantine() -> DataFrame:
    return silver_transforms.select_quarantined(dlt.read("silver.ieee_cis_gated"))
