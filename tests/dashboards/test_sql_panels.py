"""Databricks-SQL panels execute against the local-Spark gold/silver sample (schema
consistency); PromQL panels are structurally validated."""

from __future__ import annotations

from ._helpers import (
    DATABRICKS_DS_UID,
    PROMETHEUS_DS_UID,
    is_valid_promql,
    iter_targets,
    load_dashboards,
)

_DASHBOARDS = load_dashboards()


def _sql_targets():
    for name, dashboard in _DASHBOARDS.items():
        for panel, target in iter_targets(dashboard):
            if "rawSql" in target:
                assert panel["datasource"]["uid"] == DATABRICKS_DS_UID
                yield name, panel["title"], target["rawSql"]


def _promql_targets():
    for name, dashboard in _DASHBOARDS.items():
        for panel, target in iter_targets(dashboard):
            if "expr" in target:
                assert panel["datasource"]["uid"] == PROMETHEUS_DS_UID
                yield name, panel["title"], target["expr"]


def test_databricks_sql_panels_execute_against_gold_sample(sample_lakehouse):
    from ._helpers import prepare_sql_for_spark

    spark = sample_lakehouse
    executed = 0
    for _name, _title, raw_sql in _sql_targets():
        prepared = prepare_sql_for_spark(raw_sql)
        # Executing proves every referenced column exists in the real gold/silver schema.
        spark.sql(prepared).collect()
        executed += 1
    assert executed >= 8  # all the SQL panels across dashboards ran


def test_promql_panels_are_structurally_valid():
    checked = 0
    for _name, title, expr in _promql_targets():
        assert is_valid_promql(expr), f"invalid PromQL in {title}: {expr}"
        assert "$environment" in expr  # uses the template variable
        checked += 1
    assert checked >= 4


def test_feature_panel_uses_real_gold_columns(sample_lakehouse):
    from ._helpers import prepare_sql_for_spark

    spark = sample_lakehouse
    # The MCC-tier panel queries gold.txn_features_realtime — the canonical 15-feature
    # table — so this asserts the dashboard matches the real gold feature schema.
    sql = next(
        raw for _n, title, raw in _sql_targets() if title == "Avg amount z-score by MCC risk tier"
    )
    rows = spark.sql(prepare_sql_for_spark(sql)).collect()
    assert rows
    assert {"mcc_risk_tier", "avg_amount_zscore", "n"} <= set(rows[0].asDict())
