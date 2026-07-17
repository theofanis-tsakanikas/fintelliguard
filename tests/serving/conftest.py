"""Shared fixtures for serving tests: a small trained model + cwd isolation.

`mlflow.*.log_model` writes a local `mlruns/` in the CWD; run each test from a tmp dir.
"""

from __future__ import annotations

import math

import pytest
from xgboost import XGBClassifier

from ml.training.dataset import make_synthetic_frame, prepare_xy

# A canonical 15-feature sample with mixed native types (int / bool / float).
SAMPLE_FEATURES = {
    "amount_usd": 250.0,
    "amount_log": math.log1p(250.0),
    "amount_zscore": 3.5,
    "txn_velocity_1h": 9,
    "txn_velocity_24h": 12,
    "amount_sum_1h": 2200.0,
    "distinct_merchants_24h": 4,
    "card_age_days": 30,
    "device_seen_before": False,
    "device_txn_count_24h": 5,
    "country_mismatch": True,
    "distinct_countries_24h": 2,
    "mcc_risk_tier": 5,
    "is_unusual_hour": True,
}


@pytest.fixture(scope="session")
def trained_xgb():
    frame = make_synthetic_frame(n_rows=500, seed=7)
    x, y = prepare_xy(frame)
    model = XGBClassifier(
        n_estimators=40,
        max_depth=3,
        learning_rate=0.2,
        tree_method="hist",
        n_jobs=1,
        eval_metric="auc",
    )
    model.fit(x, y)
    return model


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
