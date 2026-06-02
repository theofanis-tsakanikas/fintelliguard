"""Supervisor classification + routing."""

from __future__ import annotations

from agents.langgraph.config import HealingConfig
from agents.langgraph.state import (
    FAILURE_ENDPOINT,
    FAILURE_LAG,
    FAILURE_PIPELINE,
)
from agents.langgraph.supervisor import (
    ROUTE_END,
    ROUTE_MEDIC,
    classify,
    route_after_supervisor,
    supervisor_node,
)

from .conftest import (
    degraded_endpoint_signals,
    healthy_signals,
    high_lag_signals,
    pipeline_failed_signals,
)

_CONFIG = HealingConfig()


def test_healthy_classifies_to_none():
    assert classify(healthy_signals(), _CONFIG) is None


def test_endpoint_latency_breach():
    incident = classify(degraded_endpoint_signals(), _CONFIG)
    assert incident["failure_class"] == FAILURE_ENDPOINT
    assert incident["fingerprint"] == "endpoint_latency:fraud-score"


def test_consumer_lag_breach():
    incident = classify(high_lag_signals(), _CONFIG)
    assert incident["failure_class"] == FAILURE_LAG


def test_pipeline_failure_has_highest_priority():
    # A failed pipeline AND a slow endpoint -> pipeline wins.
    signals = pipeline_failed_signals()
    signals["endpoint_p99"] = {"endpoint": "fraud-score", "p99_ms": 999.0}
    incident = classify(signals, _CONFIG)
    assert incident["failure_class"] == FAILURE_PIPELINE


def test_supervisor_node_records_incident_and_decision():
    state = supervisor_node({"signals": degraded_endpoint_signals(), "decisions": []}, _CONFIG)
    assert state["incident"]["failure_class"] == FAILURE_ENDPOINT
    assert state["decisions"][-1] == "supervisor: endpoint_latency"


def test_routing():
    assert route_after_supervisor({"incident": {"failure_class": FAILURE_ENDPOINT}}) == ROUTE_MEDIC
    assert route_after_supervisor({"incident": None}) == ROUTE_END
