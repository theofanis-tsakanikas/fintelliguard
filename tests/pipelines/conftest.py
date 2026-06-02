"""Local Spark session for the pipeline tests.

We do NOT skip on a missing Spark/Java — these tests must really run (per the task). The
fixture pins PySpark's worker interpreter to this venv (avoiding a driver/worker Python
mismatch), puts the repo root on the worker PYTHONPATH (so `ml.features` imports inside
applyInPandas), and best-effort discovers JAVA_HOME.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _discover_java_home() -> str | None:
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
        "/usr/local/opt/openjdk@17",
        *sorted(glob.glob("/usr/lib/jvm/*")),
    ]
    return next((c for c in candidates if os.path.isdir(c)), None)


def _prepare_env() -> None:
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    existing = os.environ.get("PYTHONPATH", "")
    parts = existing.split(os.pathsep) if existing else []
    if _REPO_ROOT not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([_REPO_ROOT, *parts]) if parts else _REPO_ROOT
    if not os.environ.get("JAVA_HOME"):
        java_home = _discover_java_home()
        if java_home:
            os.environ["JAVA_HOME"] = java_home


@pytest.fixture(scope="session")
def spark():
    _prepare_env()
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]")
        .appName("fintelliguard-pipelines-tests")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.executorEnv.PYTHONPATH", os.environ["PYTHONPATH"])
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()
