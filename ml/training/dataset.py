"""Training data access — by dependency injection so training stays cloud-free in tests.

In production the features come from `gold.txn_features_training` / the Feature Store
offline store (point-in-time joins). The training function, however, RECEIVES a
features+label DataFrame, so tests pass a synthetic frame and never touch the cloud.

The feature columns are exactly the canonical 15 from `ml/features` — parity is enforced
here, and the label is never one of them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.features.schema import FEATURE_NAMES

FEATURE_COLUMNS: list[str] = list(FEATURE_NAMES)
LABEL_COLUMN = "is_fraud"

# Invariant: the label is never a feature.
assert LABEL_COLUMN not in FEATURE_COLUMNS


def prepare_xy(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Split a features+label frame into (X, y) on EXACTLY the canonical 15 features.

    Raises if any feature or the label is missing. X is numeric (bools -> 0/1) with
    columns in canonical order; the label is excluded from X.
    """
    required = [*FEATURE_COLUMNS, LABEL_COLUMN]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"training frame missing columns: {missing}")

    x = frame[FEATURE_COLUMNS].astype("float64")
    y = frame[LABEL_COLUMN].astype("int64")
    if list(x.columns) != FEATURE_COLUMNS:
        raise ValueError("feature columns/order do not match the canonical schema")
    return x, y


def load_gold_training_features(spark, table: str = "fintelliguard.gold.txn_features_training"):
    """Production loader: read the gold training table into pandas (cloud — not used in tests)."""
    return spark.read.table(table).toPandas()


def make_synthetic_frame(n_rows: int = 600, seed: int = 0, base_rate: float = 0.12) -> pd.DataFrame:
    """A small synthetic features+label frame with learnable (not high-quality) signal.

    Values respect the canonical feature ranges; the label depends on a few features so a
    model can fit *some* signal. Quality is NOT expected to meet the promotion gate.
    """
    rng = np.random.default_rng(seed)

    amount_usd = np.exp(rng.normal(3.5, 1.0, n_rows)).clip(0.01, 1_000_000 - 1)
    velocity_1h = rng.poisson(1.0, n_rows)
    velocity_24h = velocity_1h + rng.poisson(2.0, n_rows)
    country_mismatch = rng.random(n_rows) < 0.10
    is_unusual_hour = rng.random(n_rows) < 0.20
    amount_zscore = rng.normal(0.0, 1.0, n_rows)

    frame = pd.DataFrame(
        {
            "amount_usd": amount_usd,
            "amount_log": np.log1p(amount_usd),
            "amount_zscore": amount_zscore,
            "txn_velocity_1h": velocity_1h.astype("int64"),
            "txn_velocity_24h": velocity_24h.astype("int64"),
            "amount_sum_1h": amount_usd * (1 + velocity_1h),
            "distinct_merchants_24h": rng.poisson(2.0, n_rows).astype("int64"),
            "card_age_days": rng.integers(0, 400, n_rows).astype("int64"),
            "device_seen_before": rng.random(n_rows) < 0.7,
            "device_txn_count_24h": (rng.poisson(1.0, n_rows) + 1).astype("int64"),
            "country_mismatch": country_mismatch,
            "distinct_countries_24h": (1 + country_mismatch.astype("int64")).astype("int64"),
            "mcc_risk_tier": rng.integers(1, 6, n_rows).astype("int64"),
            "is_unusual_hour": is_unusual_hour,
        }
    )

    logit = (
        np.log(base_rate / (1 - base_rate))
        + 2.5 * country_mismatch
        + 1.5 * is_unusual_hour
        + 0.20 * amount_zscore
        + 0.10 * velocity_1h
    )
    prob = 1.0 / (1.0 + np.exp(-logit))
    frame[LABEL_COLUMN] = (rng.random(n_rows) < prob).astype("int64")
    return frame
