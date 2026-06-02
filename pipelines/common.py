"""Shared, pure Spark helpers for the medallion pipelines.

The quarantine helpers implement "route bad rows out, don't silently drop": a
`_quarantine_reason` column is added (null when a row passes every gate, else the first
failing gate's name), and the two `select_*` functions split the frame accordingly.
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from ml.features.schema import FEATURE_SPECS, LOOKUP_KEY, PRIMARY_KEY

QUARANTINE_COL = "_quarantine_reason"

# Canonical Python feature type -> Spark type.
_SPARK_TYPE: dict[type, T.DataType] = {
    float: T.DoubleType(),
    int: T.LongType(),
    bool: T.BooleanType(),
}


def feature_fields() -> list[T.StructField]:
    """Spark struct fields for the canonical 15 features, in schema order."""
    return [T.StructField(spec.name, _SPARK_TYPE[spec.dtype], True) for spec in FEATURE_SPECS]


def feature_record_schema(extra_fields: list[T.StructField] | None = None) -> T.StructType:
    """Schema for a feature record: keys + the 15 features (+ optional extras like label)."""
    fields = [
        T.StructField(PRIMARY_KEY, T.StringType(), False),
        T.StructField(LOOKUP_KEY, T.StringType(), False),
        *feature_fields(),
    ]
    if extra_fields:
        fields.extend(extra_fields)
    return T.StructType(fields)


def add_quarantine_reason(df: DataFrame, gates: dict[str, str]) -> DataFrame:
    """Append `_quarantine_reason`: the first failing gate name, or null if all pass.

    A gate is a boolean SQL expression that must be TRUE to pass. A null result counts as
    a failure (so missing/garbled values are quarantined, never silently accepted).
    """
    reason = F.lit(None).cast(T.StringType())
    # Build from last gate to first so the first failing gate wins (outermost `when`).
    for name, expr in reversed(list(gates.items())):
        fails = ~F.coalesce(F.expr(expr), F.lit(False))
        reason = F.when(fails, F.lit(name)).otherwise(reason)
    return df.withColumn(QUARANTINE_COL, reason)


def select_valid(df: DataFrame) -> DataFrame:
    """Rows that passed every gate (drops the reason column)."""
    return df.filter(F.col(QUARANTINE_COL).isNull()).drop(QUARANTINE_COL)


def select_quarantined(df: DataFrame) -> DataFrame:
    """Rows that failed a gate, keeping `_quarantine_reason` for inspection."""
    return df.filter(F.col(QUARANTINE_COL).isNotNull())
