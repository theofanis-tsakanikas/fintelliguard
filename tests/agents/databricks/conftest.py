"""Fixtures for the copilot logic tests: local Spark, a sample transactions table, model.

Spark is required for the query_lakehouse tests (not skipped). The fixture pins PySpark's
worker interpreter to this venv and best-effort discovers JAVA_HOME.
"""

from __future__ import annotations

import glob
import math
import os
import subprocess
import sys

import pytest
from xgboost import XGBClassifier

from ml.training.dataset import make_synthetic_frame, prepare_xy

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

_TXN_SCHEMA = (
    "transaction_id string, card_hash string, merchant_id string, device_id string, "
    "ip_country string, amount double, is_fraud int"
)

# Known sample so assertions are exact (see test_query_lakehouse).
_TXN_ROWS = [
    ("t1", "C1", "M1", "D1", "DE", 100.0, 0),
    ("t2", "C1", "M1", "D2", "DE", 50.0, 1),
    ("t3", "C2", "M1", "D1", "US", 75.0, 0),
    ("t4", "C1", "M2", "D1", "FR", 20.0, 0),
    ("t5", "C3", "M2", "D3", "US", 60.0, 1),
    ("t6", "C1", "M3", "D1", "DE", 10.0, 0),
]

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


def _discover_java_home():
    try:
        result = subprocess.run(
            ["/usr/libexec/java_home"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, OSError):
        pass
    candidates = [
        "/opt/homebrew/opt/openjdk@17",
        "/opt/homebrew/opt/openjdk@11",
        "/opt/homebrew/opt/openjdk",
        *sorted(glob.glob("/usr/lib/jvm/*")),
    ]
    return next((c for c in candidates if os.path.isdir(c)), None)


@pytest.fixture(scope="session")
def spark():
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    if not os.environ.get("JAVA_HOME"):
        java_home = _discover_java_home()
        if java_home:
            os.environ["JAVA_HOME"] = java_home

    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]")
        .appName("fintelliguard-copilot-tests")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture
def transactions(spark):
    return spark.createDataFrame(_TXN_ROWS, _TXN_SCHEMA)


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
