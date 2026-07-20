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


def _add_sync_root_to_path() -> None:
    """Put the directory ABOVE `pipelines/` on sys.path.

    DLT executes each source file like a notebook cell, not as a module inside a package,
    so `from . import bronze_transforms` raised

        ImportError: attempted relative import with no known parent package

    on the first real pipeline run. `resources/pipelines.yml` predicted this — "the @dlt
    files use relative imports ... packaging the modules as a wheel is a deploy-phase
    refinement" — and a wheel is still the tidier long-term answer. This is the smaller one:
    the bundle already syncs the repository layout to the workspace, so the package is there
    and only needs to be reachable.

    `__file__` is checked but not relied on: it is undefined in some Databricks execution
    contexts (the seed job died on exactly that). cwd and sys.argv[0] are the fallbacks, and
    every candidate walks UP looking for the package rather than counting directories, so
    moving this file cannot silently break it.
    """
    import pathlib
    import sys

    candidates = []
    for value in (globals().get("__file__"), (sys.argv[0] if sys.argv else None)):
        if value:
            candidates.append(pathlib.Path(value).resolve().parent)
    candidates.append(pathlib.Path.cwd().resolve())

    for start in candidates:
        for directory in (start, *start.parents):
            if (directory / "pipelines" / "__init__.py").is_file():
                if str(directory) not in sys.path:
                    sys.path.insert(0, str(directory))
                return
    raise ImportError(
        "cannot locate the `pipelines` package from "
        f"{[str(c) for c in candidates]} — check `sync.paths` in infra/bundles/databricks.yml"
    )


_add_sync_root_to_path()

from pipelines.bronze import bronze_transforms  # noqa: E402

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
