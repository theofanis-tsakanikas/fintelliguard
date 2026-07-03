"""Thin DLT layer for bronze — runs only on Databricks.

The decorators call the pure transforms in `bronze_transforms.py`. Streaming sources
(Kafka, Auto Loader) and the `spark`/`dlt` globals exist on Databricks; locally this
module is import/lint-validated only (see pipelines/README.md).
"""

from __future__ import annotations

import os

import dlt
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from . import bronze_transforms

# Provided by Databricks at runtime; None locally (functions below are not called here).
spark = SparkSession.getActiveSession()

KAFKA_TOPIC = "txn.raw"

# The raw IEEE-CIS location is env-driven so it matches the Terraform-provisioned bucket
# (`fintelliguard-raw-<account_id>`, exposed as the `raw_bucket_name` output in
# infra/aws/outputs.tf). The DABs job injects `RAW_BUCKET` (or the full `IEEE_RAW_PATH`)
# from that output — the old hardcoded `fintelliguard-raw` never matched the real bucket
# name, so Auto Loader would 404 on the first real run.
_RAW_BUCKET = os.environ.get("RAW_BUCKET", "fintelliguard-raw")
IEEE_RAW_PATH = os.environ.get("IEEE_RAW_PATH", f"s3://{_RAW_BUCKET}/raw/ieee-cis/")


@dlt.table(
    name="bronze.transactions_stream",
    comment="Raw simulator transactions from Kafka — schema-rescued, with ingest metadata.",
)
def transactions_stream() -> DataFrame:
    raw = (
        spark.readStream.format("kafka")
        .option("subscribe", KAFKA_TOPIC)
        .load()
        .select(F.col("value").cast("string").alias("value"), F.col("offset").alias("offset"))
    )
    return bronze_transforms.parse_transactions_stream(raw)


@dlt.table(
    name="bronze.ieee_cis_raw",
    comment="IEEE-CIS raw via Auto Loader — schema-rescued, with row_hash.",
)
def ieee_cis_raw() -> DataFrame:
    raw = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("cloudFiles.schemaEvolutionMode", "rescue")
        .load(IEEE_RAW_PATH)
    )
    return bronze_transforms.parse_ieee_raw(raw)
