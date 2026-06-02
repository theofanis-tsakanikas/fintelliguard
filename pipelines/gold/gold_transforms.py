"""Gold transforms: produce the 15 features by WIRING the tested ml/features adapters.

The feature logic is NOT reimplemented here. Each layer applies the already-unit-tested
pure functions over a per-card group via Spark `applyInPandas`:

- realtime (serving): `adapter_stream.compute_features` over each card's history, in
  timestamp order — exactly the streaming semantics, computed group-wise.
- training (batch): `adapter_ieee.map_row` with per-card1 aggregates (CardContext).

`gate_features` applies the docs/features.md validation gates and routes failures to
quarantine (via pipelines.common), never dropping silently.
"""

from __future__ import annotations

import math

import pandas as pd
from pyspark.sql import DataFrame
from pyspark.sql import types as T

from ml.features.adapter_ieee import CardContext, map_row
from ml.features.adapter_stream import compute_features
from ml.features.schema import FEATURE_NAMES, LOOKUP_KEY, PRIMARY_KEY
from pipelines.common import (
    add_quarantine_reason,
    feature_record_schema,
    select_quarantined,
    select_valid,
)

__all__ = [
    "GOLD_GATES",
    "build_realtime_features",
    "build_training_features",
    "gate_features",
    "select_quarantined",
    "select_valid",
]

# The eight bronze-contract fields adapter_stream consumes.
_CONTRACT_KEYS = (
    "transaction_id",
    "timestamp",
    "amount",
    "merchant_id",
    "card_hash",
    "device_id",
    "ip_country",
    "mcc_code",
)

_REALTIME_SCHEMA = feature_record_schema()
_TRAINING_SCHEMA = feature_record_schema([T.StructField("is_fraud", T.LongType(), True)])

# DLT feature gates — exactly the validation gates from docs/features.md.
_NO_NULL_FEATURES = " AND ".join(f"{name} IS NOT NULL" for name in FEATURE_NAMES)
GOLD_GATES: dict[str, str] = {
    "amount_in_range": "amount_usd > 0 AND amount_usd < 1000000",
    "velocity_nonneg": "txn_velocity_1h >= 0",
    "velocity_monotonic": "txn_velocity_24h >= txn_velocity_1h",
    "merchant_risk_unit": "merchant_risk_score >= 0 AND merchant_risk_score <= 1",
    "mcc_tier_valid": "mcc_risk_tier IN (1, 2, 3, 4, 5)",
    "no_null_features": _NO_NULL_FEATURES,
}


def _realtime_group(pdf: pd.DataFrame) -> pd.DataFrame:
    """Per-card: replay events in time order through `compute_features` (no leakage)."""
    pdf = pdf.sort_values(["timestamp", "transaction_id"])
    history: list[dict] = []
    out: list[dict] = []
    for record in pdf.to_dict("records"):
        current = {key: record[key] for key in _CONTRACT_KEYS}
        current["amount"] = float(current["amount"])
        result = compute_features(current, history)
        out.append(
            {
                PRIMARY_KEY: result.transaction_id,
                LOOKUP_KEY: result.card_hash,
                **result.features.as_dict(),
            }
        )
        history.append(current)
    return pd.DataFrame(out, columns=[PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES])


def build_realtime_features(silver_clean: DataFrame) -> DataFrame:
    """gold.txn_features_realtime — the 15 features from clean stream transactions."""
    return silver_clean.groupBy(LOOKUP_KEY).applyInPandas(_realtime_group, schema=_REALTIME_SCHEMA)


def _training_group(pdf: pd.DataFrame) -> pd.DataFrame:
    """Per-card1: compute group aggregates, then map each row via adapter_ieee."""
    amounts = pd.to_numeric(pdf["TransactionAmt"], errors="coerce")
    mean = float(amounts.mean()) if amounts.notna().any() else 0.0
    std = float(amounts.std(ddof=1)) if amounts.notna().sum() > 1 else 0.0
    if math.isnan(mean):
        mean = 0.0
    if math.isnan(std):
        std = 0.0
    addr2_mode = pd.to_numeric(pdf["addr2"], errors="coerce").dropna().mode()
    modal_addr2 = float(addr2_mode.iloc[0]) if not addr2_mode.empty else None
    ctx = CardContext(amount_mean=mean, amount_std=std, modal_addr2=modal_addr2)

    out: list[dict] = []
    for record in pdf.to_dict("records"):
        result = map_row(record, ctx)
        row = {
            PRIMARY_KEY: result.transaction_id,
            LOOKUP_KEY: result.card_hash,
            **result.features.as_dict(),
        }
        row["is_fraud"] = int(record["isFraud"])
        out.append(row)
    return pd.DataFrame(out, columns=[PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES, "is_fraud"])


def build_training_features(silver_ieee_clean: DataFrame) -> DataFrame:
    """gold.txn_features_training — the same 15 features + isFraud label, from IEEE-CIS."""
    return silver_ieee_clean.groupBy("card1").applyInPandas(
        _training_group, schema=_TRAINING_SCHEMA
    )


def gate_features(features: DataFrame) -> DataFrame:
    """Tag each feature row with a quarantine reason per the docs/features.md gates."""
    return add_quarantine_reason(features, GOLD_GATES)
