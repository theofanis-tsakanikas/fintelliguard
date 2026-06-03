"""Shared helpers for the dashboard tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = _REPO_ROOT / "dashboards" / "grafana"
PROVISIONING_DIR = _REPO_ROOT / "dashboards" / "provisioning"

DATABRICKS_DS_UID = "fintelliguard-databricks"
PROMETHEUS_DS_UID = "fintelliguard-prometheus"
DATABRICKS_DS_TYPE = "grafana-databricks-datasource"
PROMETHEUS_DS_TYPE = "prometheus"


def load_dashboards() -> dict[str, dict[str, Any]]:
    """Parse every dashboard JSON, keyed by file stem."""
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(DASHBOARD_DIR.glob("*.json"))
    }


def iter_targets(dashboard: dict[str, Any]):
    """Yield (panel, target) for every target in a dashboard."""
    for panel in dashboard.get("panels", []):
        for target in panel.get("targets", []):
            yield panel, target


def prepare_sql_for_spark(raw_sql: str) -> str:
    """Rewrite Grafana macros so a panel query runs in local Spark.

    `$__timeFilter(col)` becomes a no-op predicate on the column (it must still exist).
    """
    return re.sub(r"\$__timeFilter\(\s*([^)]+?)\s*\)", r"(\1 IS NOT NULL)", raw_sql)


def is_valid_promql(expr: str) -> bool:
    """Cheap structural validation (no Prometheus available locally)."""
    expr = expr.strip()
    if not expr or "$__" in expr:  # Grafana SQL macros must not appear in PromQL
        return False
    if expr.count("(") != expr.count(")"):
        return False
    if expr.count("{") != expr.count("}"):
        return False
    return bool(re.search(r"[a-zA-Z_:][a-zA-Z0-9_:]*", expr))
