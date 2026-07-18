"""Every dashboard JSON parses and conforms to the expected structure."""

from __future__ import annotations

import pytest

from ._helpers import (
    DATABRICKS_DS_UID,
    PROMETHEUS_DS_UID,
    iter_targets,
    load_dashboards,
)

_DASHBOARDS = load_dashboards()
_VALID_UIDS = {DATABRICKS_DS_UID, PROMETHEUS_DS_UID}

# Required panel titles per dashboard (by file stem).
_REQUIRED_PANELS = {
    "fraud_overview": {
        "Fraud score distribution",
        "Decision mix",
        "Tier-2 escalation rate",
        "Top merchant risk",
        "Top country risk",
    },
    "pipeline_health": {
        "DLT pipeline status",
        "Kafka consumer lag",
        "Silver ingestion throughput",
        "Quarantine counts by reason",
    },
    "serving_latency": {
        "get_fraud_score p99 latency",
        "get_fraud_score p50 latency",
        "Request rate",
        "Served model version",
    },
    "compliance": {
        "Verdict counts",
        "Guardrail block rate",
        "Tier-2 verdict latency p99",
    },
    # The local funnel dashboard queries ONLY the series the docker scorer actually emits
    # (fintelliguard_* + model_serving_*), so it is the one dashboard that fully populates
    # with no cloud. The production four target the real cloud metric taxonomy.
    "local_funnel": {
        "Tier-1 throughput by decision (req/s)",
        "Fraud-score distribution (p50 / p90 / p99)",
        "get_fraud_score latency (p50 / p99)",
        "Tier-2 verdict-gate outcomes (rate)",
        "Decision-log refusals (must be 0)",
    },
}


def test_all_expected_dashboards_present():
    assert set(_DASHBOARDS) == set(_REQUIRED_PANELS)


@pytest.mark.parametrize("name", sorted(_DASHBOARDS))
def test_dashboard_top_level_structure(name):
    dashboard = _DASHBOARDS[name]
    for key in ("uid", "title", "schemaVersion", "panels", "templating"):
        assert key in dashboard, f"{name} missing {key}"
    assert isinstance(dashboard["panels"], list) and dashboard["panels"]


@pytest.mark.parametrize("name", sorted(_DASHBOARDS))
def test_environment_template_variable(name):
    variables = _DASHBOARDS[name]["templating"]["list"]
    assert any(v.get("name") == "environment" for v in variables)


@pytest.mark.parametrize("name", sorted(_DASHBOARDS))
def test_panels_have_datasource_and_targets(name):
    for panel in _DASHBOARDS[name]["panels"]:
        assert panel.get("title")
        assert panel.get("type")
        datasource = panel.get("datasource", {})
        assert datasource.get("uid") in _VALID_UIDS, f"{name}/{panel.get('title')} bad datasource"
        targets = panel.get("targets", [])
        assert targets, f"{name}/{panel['title']} has no targets"
        for target in targets:
            # exactly one of rawSql / expr
            assert ("rawSql" in target) ^ ("expr" in target)


@pytest.mark.parametrize("name", sorted(_DASHBOARDS))
def test_required_panels_present(name):
    titles = {panel["title"] for panel in _DASHBOARDS[name]["panels"]}
    assert _REQUIRED_PANELS[name] <= titles


def test_datasource_type_matches_target_kind():
    # Databricks panels carry rawSql; Prometheus panels carry expr.
    for dashboard in _DASHBOARDS.values():
        for panel, target in iter_targets(dashboard):
            uid = panel["datasource"]["uid"]
            if uid == DATABRICKS_DS_UID:
                assert "rawSql" in target
            elif uid == PROMETHEUS_DS_UID:
                assert "expr" in target
