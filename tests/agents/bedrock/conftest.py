"""Fixtures for the Bedrock Lambda tests.

The Lambda modules live in `agents/bedrock/lambda/` and are flat top-level modules (that
dir is the zip root; `lambda` is a Python keyword). We put it on sys.path so they import
exactly as they would in the Lambda runtime.
"""

from __future__ import annotations

import math
import os
import sys

import pytest
from xgboost import XGBClassifier

from ml.training.dataset import make_synthetic_frame, prepare_xy

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_LAMBDA_DIR = os.path.join(_REPO_ROOT, "agents", "bedrock", "lambda")
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)


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
    frame = make_synthetic_frame(n_rows=400, seed=7)
    x, y = prepare_xy(frame)
    model = XGBClassifier(
        n_estimators=30,
        max_depth=3,
        learning_rate=0.2,
        tree_method="hist",
        n_jobs=1,
        eval_metric="auc",
    )
    model.fit(x, y)
    return model


@pytest.fixture
def sample_features():
    return dict(SAMPLE_FEATURES)
