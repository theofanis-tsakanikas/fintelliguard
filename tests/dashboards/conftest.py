"""Local Spark + a sample gold/silver lakehouse matching the real schemas.

The sample `gold.txn_features_realtime` table uses the EXACT canonical feature schema
(pipelines.common.feature_record_schema), so the dashboard SQL is cross-checked against
the real gold schema.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

from pipelines.common import feature_record_schema

_BASE_TS = datetime(2026, 1, 1, 12, 0, 0)


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

    warehouse = tempfile.mkdtemp(prefix="fg-dash-warehouse-")
    session = (
        SparkSession.builder.master("local[1]")
        .appName("fintelliguard-dashboards-tests")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.warehouse.dir", warehouse)
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture(scope="session")
def sample_lakehouse(spark):
    """Create sample gold/silver tables the dashboard panels query.

    Databases get an explicit tmp LOCATION so managed-table data never lands in (or
    collides with) the repo's default ./spark-warehouse — robust even when an earlier
    Spark test already created the shared session (whose warehouse.dir we can't change).
    """
    db_root = tempfile.mkdtemp(prefix="fg-dash-db-")
    spark.sql(f"CREATE DATABASE IF NOT EXISTS gold LOCATION '{db_root}/gold'")
    spark.sql(f"CREATE DATABASE IF NOT EXISTS silver LOCATION '{db_root}/silver'")

    # gold.txn_features_realtime — EXACT canonical 15-feature schema.
    feature_rows = [
        ("t1", "c1", 100.0, 4.62, 0.5, 2, 5, 210.0, 3, 30, True, 4, False, 1, 0.2, 1, False),
        ("t2", "c2", 5000.0, 8.52, 6.0, 9, 12, 12000.0, 5, 2, False, 7, True, 3, 0.9, 5, True),
    ]
    spark.createDataFrame(feature_rows, schema=feature_record_schema()).write.mode(
        "overwrite"
    ).saveAsTable("gold.txn_features_realtime")

    scored = [
        ("t1", "c1", _BASE_TS, 0.12, "allow", "fraud-xgb:3", "M1", "DE", 1),
        ("t2", "c2", _BASE_TS + timedelta(minutes=1), 0.86, "review", "fraud-xgb:3", "M1", "US", 3),
        ("t3", "c3", _BASE_TS + timedelta(minutes=2), 0.95, "block", "fraud-xgb:3", "M2", "BR", 5),
    ]
    spark.createDataFrame(
        scored,
        "transaction_id string, card_hash string, event_time timestamp, fraud_score double, "
        "decision_hint string, model_version string, merchant_id string, ip_country string, "
        "mcc_risk_tier int",
    ).write.mode("overwrite").saveAsTable("gold.scored_transactions")

    verdicts = [
        ("k1", "t2", _BASE_TS, "review", False, 1800.0, True),
        ("k2", "t3", _BASE_TS + timedelta(minutes=1), "block", True, 2400.0, True),
    ]
    spark.createDataFrame(
        verdicts,
        "case_id string, transaction_id string, event_time timestamp, verdict string, "
        "guardrail_blocked boolean, verdict_latency_ms double, tier2_triggered boolean",
    ).write.mode("overwrite").saveAsTable("gold.case_verdicts")

    quarantine = [
        ("silver", "amount_positive_bounded", "tq1", _BASE_TS),
        ("silver", "valid_card_hash", "tq2", _BASE_TS),
        ("gold", "mcc_tier_valid", "tq3", _BASE_TS),
    ]
    spark.createDataFrame(
        quarantine, "layer string, reason string, transaction_id string, event_time timestamp"
    ).write.mode("overwrite").saveAsTable("gold.quarantine_events")

    txns = [
        ("t1", "2026-01-01T12:00:00", _BASE_TS, 100.0, "M1", "c1", "D1", "DE", "5411", 1),
        (
            "t2",
            "2026-01-01T12:01:00",
            _BASE_TS + timedelta(minutes=1),
            5000.0,
            "M1",
            "c2",
            "D2",
            "US",
            "7995",
            5,
        ),
    ]
    spark.createDataFrame(
        txns,
        "transaction_id string, timestamp string, event_time timestamp, amount double, "
        "merchant_id string, card_hash string, device_id string, ip_country string, "
        "mcc_code string, mcc_risk_tier int",
    ).write.mode("overwrite").saveAsTable("silver.transactions_clean")

    return spark
