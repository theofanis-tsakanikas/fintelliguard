"""Pure Spark transforms for the bronze layer (df_in -> df_out, locally testable).

No DLT, no streaming source — `bronze_pipeline.py` supplies those and calls these.
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

# The simulator's bronze stream contract (docs/data-flow.md, topic txn.raw).
CONTRACT_SCHEMA = T.StructType(
    [
        T.StructField("transaction_id", T.StringType()),
        T.StructField("timestamp", T.StringType()),
        T.StructField("amount", T.DoubleType()),
        T.StructField("merchant_id", T.StringType()),
        T.StructField("card_hash", T.StringType()),
        T.StructField("device_id", T.StringType()),
        T.StructField("ip_country", T.StringType()),
        T.StructField("mcc_code", T.StringType()),
    ]
)

_CONTRACT_FIELDS = [f.name for f in CONTRACT_SCHEMA.fields]
STREAM_SOURCE = "kafka:txn.raw"
IEEE_SOURCE = "autoloader:ieee-cis"

# Parse schema = the contract plus a corrupt-record column. PERMISSIVE mode routes
# unparseable payloads into `_rescued_data` (with null contract fields) instead of
# dropping them.
_RESCUE_COL = "_rescued_data"
_PARSE_SCHEMA = T.StructType([*CONTRACT_SCHEMA.fields, T.StructField(_RESCUE_COL, T.StringType())])
_PARSE_OPTIONS = {"mode": "PERMISSIVE", "columnNameOfCorruptRecord": _RESCUE_COL}


def parse_transactions_stream(raw: DataFrame) -> DataFrame:
    """Parse Kafka JSON payloads into the contract, rescuing unparseable rows.

    `raw` carries a string `value` (the JSON) and a long `offset` (Kafka metadata).
    Rows whose JSON cannot be parsed keep their raw payload in `_rescued_data` with null
    contract fields, rather than being dropped.
    """
    parsed = raw.withColumn("data", F.from_json(F.col("value"), _PARSE_SCHEMA, _PARSE_OPTIONS))
    return parsed.select(
        *[F.col(f"data.{name}").alias(name) for name in _CONTRACT_FIELDS],
        F.col(f"data.{_RESCUE_COL}").alias(_RESCUE_COL),
        F.current_timestamp().alias("ingest_timestamp"),
        F.lit(STREAM_SOURCE).alias("source"),
        F.col("offset").alias("offset"),
    )


def parse_ieee_raw(raw: DataFrame) -> DataFrame:
    """Stamp IEEE-CIS Auto Loader rows with a stable `row_hash` + ingest metadata.

    Auto Loader contributes `_rescued_data` (added here if absent for local tests).
    `row_hash` is a deterministic content hash for dedupe/lineage.
    """
    content_cols = [c for c in raw.columns if c != "_rescued_data"]
    row_hash = F.sha2(
        F.concat_ws("||", *[F.coalesce(F.col(c).cast("string"), F.lit("")) for c in content_cols]),
        256,
    )
    out = (
        raw.withColumn("row_hash", row_hash)
        .withColumn("ingest_timestamp", F.current_timestamp())
        .withColumn("source", F.lit(IEEE_SOURCE))
    )
    if "_rescued_data" not in raw.columns:
        out = out.withColumn("_rescued_data", F.lit(None).cast(T.StringType()))
    return out
