"""Thin DLT layer for gold — runs only on Databricks.

Wires the tested feature transforms into DLT tables. The realtime features split into
clean / quarantine; `@dlt.expect_all(GOLD_GATES)` mirrors the gates as DLT metrics.
Import/lint-validated locally; streaming/stateful execution is deferred to deploy.
"""

from __future__ import annotations

import dlt
from pyspark.sql import DataFrame

from . import gold_transforms


@dlt.table(
    name="gold.txn_features_realtime",
    comment="The 15 serving features from the stream (validated).",
)
@dlt.expect_all(gold_transforms.GOLD_GATES)
def txn_features_realtime() -> DataFrame:
    features = gold_transforms.build_realtime_features(dlt.read("silver.transactions_clean"))
    return gold_transforms.select_valid(gold_transforms.gate_features(features))


@dlt.table(
    name="gold.txn_features_realtime_quarantine",
    comment="Realtime feature rows failing a gate, kept for inspection.",
)
def txn_features_realtime_quarantine() -> DataFrame:
    features = gold_transforms.build_realtime_features(dlt.read("silver.transactions_clean"))
    return gold_transforms.select_quarantined(gold_transforms.gate_features(features))


@dlt.table(
    name="gold.txn_features_training",
    comment="The same 15 features + isFraud label, from IEEE-CIS (validated).",
)
@dlt.expect_all(gold_transforms.GOLD_GATES)
def txn_features_training() -> DataFrame:
    features = gold_transforms.build_training_features(dlt.read("silver.ieee_cis_clean"))
    return gold_transforms.select_valid(gold_transforms.gate_features(features))
