"""End-to-end self-healing graph runs (real LangGraph, mocked signals/actions)."""

from __future__ import annotations

import os

from agents.langgraph.config import HealingConfig
from agents.langgraph.graph import (
    build_self_healing_graph,
    configure_tracing,
    run_self_healing,
)

from .conftest import (
    StubMonitors,
    degraded_endpoint_signals,
    healthy_signals,
    high_lag_signals,
    pipeline_failed_signals,
)

_CONFIG = HealingConfig()


def _graph(signals, actions):
    return build_self_healing_graph(StubMonitors(signals), actions, _CONFIG)


def test_healthy_run_takes_no_action(actions, mlflow_client):
    final = run_self_healing(_graph(healthy_signals(), actions))
    assert final["incident"] is None
    assert final["actions_taken"] == []
    assert mlflow_client.transitions == []
    assert final["decisions"][-1] == "supervisor: healthy"


def test_degraded_endpoint_rolls_back(actions, mlflow_client):
    final = run_self_healing(_graph(degraded_endpoint_signals(), actions))
    assert final["incident"]["failure_class"] == "endpoint_latency"
    assert final["actions_taken"][-1]["action"] == "rollback_model"
    assert len(mlflow_client.transitions) == 1


def test_high_lag_scales(actions):
    final = run_self_healing(_graph(high_lag_signals(), actions))
    assert final["actions_taken"][-1]["action"] == "scale_consumers"
    assert actions.scale_consumers.calls == [("txn.raw", 2)]


def test_pipeline_failure_retries_then_escalates_across_cycles(actions):
    graph = _graph(pipeline_failed_signals(), actions)
    first = run_self_healing(graph)
    assert first["actions_taken"][-1]["action"] == "retry_pipeline"
    assert actions.restart_pipeline.calls == [("pl-1",)]

    # Pipeline still failing on the next cycle (carry state) -> escalate.
    second = run_self_healing(graph, first)
    assert second["actions_taken"][-1]["action"] == "escalate_pipeline"
    assert actions.escalate.calls


def test_same_incident_is_not_remediated_twice(actions, mlflow_client):
    graph = _graph(degraded_endpoint_signals(), actions)
    first = run_self_healing(graph)
    assert len(mlflow_client.transitions) == 1

    second = run_self_healing(graph, first)  # same incident, carried state
    assert len(mlflow_client.transitions) == 1  # NOT rolled back again
    assert "skip" in second["decisions"][-1]


def test_tracing_is_off_by_default_and_toggles():
    os.environ.pop("LANGCHAIN_TRACING_V2", None)
    configure_tracing(HealingConfig())
    assert os.environ.get("LANGCHAIN_TRACING_V2") != "true"

    configure_tracing(HealingConfig(enable_langsmith_tracing=True))
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    configure_tracing(HealingConfig())  # reset off
    assert os.environ.get("LANGCHAIN_TRACING_V2") != "true"
