"""Thin DLT layer for gold — runs only on Databricks.

Wires the tested feature transforms into DLT tables. Import/lint-validated locally;
streaming/stateful execution is deferred to deploy.

The expectations sit on a gated VIEW, not on the clean table — see
`pipelines/silver/silver_pipeline.py` for the full reason. In short: `@dlt.expect_all` on a
frame that `select_valid` has already filtered can only ever report 100%, because every row
that could fail it has been removed before DLT looks.
"""

from __future__ import annotations

from pathlib import Path

import dlt
from pyspark.sql import DataFrame, SparkSession

from ml.features.merchant_risk import load as load_merchant_risk

from . import gold_transforms

# Spark conf key for the merchant risk table, published alongside the model that was
# trained with it. Required: `merchant_risk_score` is one of the 15 features and it was 0.0
# in every row this table has ever produced, because no caller passed a table at all.
MERCHANT_RISK_TABLE_CONF = "fintelliguard.merchant_risk_table_path"


def _merchant_risk_table():
    """The table this pipeline scores against. Fails the update if it is missing.

    Fail-closed on purpose: an absent table used to mean a silent constant 0.0, which looks
    exactly like a working system until someone inspects the feature.
    """
    spark = SparkSession.getActiveSession()
    return load_merchant_risk(Path(spark.conf.get(MERCHANT_RISK_TABLE_CONF)))


@dlt.view(
    name="gold.txn_features_realtime_gated",
    comment="Serving features, tagged pass/fail. The DQ metric is measured here.",
)
@dlt.expect_all(gold_transforms.GOLD_GATES)
def txn_features_realtime_gated() -> DataFrame:
    # Unfiltered: a row that fails a gate must still be here for the expectation to see it.
    features = gold_transforms.build_realtime_features(
        dlt.read("silver.transactions_clean"),
        merchant_risk_table=_merchant_risk_table(),
    )
    return gold_transforms.gate_features(features)


@dlt.table(
    name="gold.txn_features_realtime",
    comment="The 15 serving features from the stream (validated).",
)
def txn_features_realtime() -> DataFrame:
    return gold_transforms.select_valid(dlt.read("gold.txn_features_realtime_gated"))


@dlt.table(
    name="gold.txn_features_realtime_quarantine",
    comment="Realtime feature rows failing a gate, kept for inspection.",
)
def txn_features_realtime_quarantine() -> DataFrame:
    return gold_transforms.select_quarantined(dlt.read("gold.txn_features_realtime_gated"))


@dlt.view(
    name="gold.txn_features_training_gated",
    comment="Training features, tagged pass/fail. The DQ metric is measured here.",
)
@dlt.expect_all(gold_transforms.GOLD_GATES)
def txn_features_training_gated() -> DataFrame:
    features = gold_transforms.build_training_features(dlt.read("silver.ieee_cis_clean"))
    return gold_transforms.gate_features(features)


@dlt.table(
    name="gold.txn_features_training",
    comment="The same 15 features + isFraud label, from IEEE-CIS (validated).",
)
def txn_features_training() -> DataFrame:
    return gold_transforms.select_valid(dlt.read("gold.txn_features_training_gated"))
