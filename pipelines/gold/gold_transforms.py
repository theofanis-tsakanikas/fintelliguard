"""Gold transforms: produce the canonical features by WIRING the tested ml/features adapters.

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
from datetime import datetime

import pandas as pd
from pyspark.sql import DataFrame
from pyspark.sql import types as T

from ml.features.adapter_ieee import CardContext, map_row
from ml.features.adapter_stream import FeatureComputationError, compute_features
from ml.features.schema import FEATURE_NAMES, LOOKUP_KEY, PRIMARY_KEY
from ml.features.transforms import modal_value
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

# The 30-day window `adapter_stream` uses, in TransactionDT's unit (seconds).
_THIRTY_DAYS_SECONDS = 30 * 24 * 3600

# DLT feature gates — exactly the validation gates from docs/features.md.
_NO_NULL_FEATURES = " AND ".join(f"{name} IS NOT NULL" for name in FEATURE_NAMES)
GOLD_GATES: dict[str, str] = {
    "amount_in_range": "amount_usd > 0 AND amount_usd < 1000000",
    "velocity_nonneg": "txn_velocity_1h >= 0",
    "velocity_monotonic": "txn_velocity_24h >= txn_velocity_1h",
    "mcc_tier_valid": "mcc_risk_tier IN (1, 2, 3, 4, 5)",
    "no_null_features": _NO_NULL_FEATURES,
}


def _realtime_group(pdf: pd.DataFrame) -> pd.DataFrame:
    """Per-card: replay events in time order through `compute_features` (no leakage).

    A row whose features cannot be computed is emitted with NULL features rather than
    dropped, so a Gold gate routes it to the quarantine table with a reason. In practice the
    reason a human reads is the FIRST gate whose SQL is non-true on the nulls —
    `amount_in_range` (a NULL `amount_usd` is not `> 0`) — and `no_null_features` is the
    backstop that guarantees the routing even if every range gate somehow passed. The point
    is that the row is quarantined with evidence, not that any single gate name catches it.
    It used to be appended to a local `quarantined` list that was never read —
    the row vanished with no counter, no log and no table, while three docstrings and this
    module's header promised "routes failures to quarantine … never dropping silently".
    Collecting evidence and discarding it is worse than not collecting it: it reads as a
    quarantine path to anyone who greps for one.
    """
    pdf = pdf.sort_values(["timestamp", "transaction_id"])
    history: list[dict] = []
    out: list[dict] = []

    # In a full-group recompute the card's earliest event IS its first-seen. Passing it
    # explicitly stops card_age_days being measured against whatever slice of history the
    # caller happens to hold.
    first_seen = _earliest_timestamp(pdf)

    for record in pdf.to_dict("records"):
        current = {key: record[key] for key in _CONTRACT_KEYS}
        current["amount"] = float(current["amount"])
        try:
            result = compute_features(current, history, card_first_seen=first_seen)
        except FeatureComputationError:
            # Keep the card group alive — `adapter_stream`'s docstring has always promised
            # the Gold batch does this, and it had no try/except at all, so one malformed
            # timestamp anywhere in a card's history killed the Spark task, took four stage
            # retries with it, and failed the whole DLT update.
            out.append(_unfeaturisable_row(current))
            continue
        out.append(
            {
                PRIMARY_KEY: result.transaction_id,
                LOOKUP_KEY: result.card_hash,
                **result.features.as_dict(),
            }
        )
        history.append(current)
    return pd.DataFrame(out, columns=[PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES])


def _unfeaturisable_row(current: dict) -> dict:
    """Keys, and nulls where the features would have been.

    A Gold gate then quarantines it with a reason a human can read, on the table that already
    exists for the purpose — instead of the row disappearing. The reason will be the first
    gate the nulls fail (`amount_in_range`), with `no_null_features` as the backstop; either
    way the row is routed, not dropped.
    """
    return {
        PRIMARY_KEY: str(current.get("transaction_id")),
        LOOKUP_KEY: str(current.get("card_hash")),
        **dict.fromkeys(FEATURE_NAMES),
    }


def _earliest_timestamp(pdf: pd.DataFrame) -> datetime | None:
    """The card's first event time, or None when nothing in the group parses."""
    parsed = pd.to_datetime(pdf["timestamp"], errors="coerce", format="mixed")
    parsed = parsed.dropna()
    return parsed.min().to_pydatetime() if not parsed.empty else None


def build_realtime_features(silver_clean: DataFrame) -> DataFrame:
    """gold.txn_features_realtime — the canonical features from clean stream transactions."""
    return silver_clean.groupBy(LOOKUP_KEY).applyInPandas(_realtime_group, schema=_REALTIME_SCHEMA)


def _training_group(pdf: pd.DataFrame) -> pd.DataFrame:
    """Per-card1: walk the card in time order, mapping each row against its OWN PAST.

    The aggregates used to be computed over the whole group:

        amounts = pd.to_numeric(pdf["TransactionAmt"])   # every row of the card...
        mean = float(amounts.mean())                     # ...including the future

    so `amount_zscore` and `country_mismatch` at training were measured against statistics
    that contained the transaction being scored and every transaction after it. The stream
    adapter computes the same two features from strictly-prior events only. The features
    shared a name and were computed under opposite information sets — and
    `docs/features.md` promised "all window/state features are computed only from data
    *before* the current transaction", which was true of the serving path and false of the
    path that built the labels.

    An expanding window costs a sort and a running total, and makes the promise true.
    """
    pdf = pdf.sort_values(["TransactionDT", "TransactionID"])

    # 30-day windows, matching `adapter_stream`'s. These were unbounded — every prior row,
    # forever — while the serving side windowed the same two aggregates to 30 days. Same
    # feature name, two different windows, either side of the train/serve boundary.
    seen: list[tuple[float, float | None, object | None]] = []  # (seconds, amount, addr2)

    out: list[dict] = []
    for record in pdf.to_dict("records"):
        now = _finite(record.get("TransactionDT")) or 0.0
        window = [row for row in seen if now - row[0] <= _THIRTY_DAYS_SECONDS]

        # Context from PRIOR rows only — this row is not yet in `seen`.
        amounts = [amount for _, amount, _ in window if amount is not None]
        addr2s = [addr for _, _, addr in window if addr is not None]
        hours = [int((seconds // 3600) % 24) for seconds, _, _ in window]

        ctx = CardContext(
            amount_mean=_mean(amounts),
            amount_std=_stddev(amounts),
            modal_addr2=modal_value(addr2s) if addr2s else None,
            active_hours=tuple(hours) or None,
            # IEEE-CIS has no device identity a batch job can resolve here, so this is left
            # absent and `map_row` falls back to C6 — a device fact. It must NOT be derived
            # from `card_age_days`, which is what made feature #9 a shadow of feature #8
            # carrying zero independent signal.
            device_seen_before=None,
        )
        result = map_row(record, ctx)
        row = {
            PRIMARY_KEY: result.transaction_id,
            LOOKUP_KEY: result.card_hash,
            **result.features.as_dict(),
        }
        row["is_fraud"] = int(record["isFraud"])
        out.append(row)

        # `_usable` for addr2, `_finite` for the amount. addr2 is a CODE, not a quantity:
        # numeric in real IEEE-CIS, but coercing it to float drops any non-numeric country
        # code silently, so `modal_addr2` came out None and `country_mismatch` was False on
        # every row. The same coercion bug the adapter had, in the caller — found by routing
        # the parity test through this function instead of a context the test invented.
        seen.append((now, _finite(record.get("TransactionAmt")), _usable(record.get("addr2"))))

    return pd.DataFrame(out, columns=[PRIMARY_KEY, LOOKUP_KEY, *FEATURE_NAMES, "is_fraud"])


def _usable(value: object) -> object | None:
    """The value, or None when it is missing. Only NaN is missing.

    Unlike `_finite`, this does not coerce: `addr2` is a country code, and turning it into a
    float makes every non-numeric code vanish.
    """
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN != NaN
        return None
    return value


def _finite(value: object) -> float | None:
    """A usable number, or None. NaN is not usable — and NaN is not NULL.

    `silver_transforms` guards these columns with `coalesce`, which replaces NULL only, so
    a NaN cell reached `int(_num(...))` in the IEEE adapter and raised `ValueError: cannot
    convert float NaN to integer`, killing the card group's Spark task. IEEE-CIS's C/D/dist
    columns are famously sparse.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def build_training_features(silver_ieee_clean: DataFrame) -> DataFrame:
    """gold.txn_features_training — the same 15 features + isFraud label, from IEEE-CIS."""
    return silver_ieee_clean.groupBy("card1").applyInPandas(
        _training_group, schema=_TRAINING_SCHEMA
    )


def gate_features(features: DataFrame) -> DataFrame:
    """Tag each feature row with a quarantine reason per the docs/features.md gates."""
    return add_quarantine_reason(features, GOLD_GATES)
