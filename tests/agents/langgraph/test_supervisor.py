"""Supervisor classification + routing."""

from __future__ import annotations

from agents.langgraph.config import HealingConfig
from agents.langgraph.state import (
    FAILURE_ENDPOINT,
    FAILURE_LAG,
    FAILURE_PIPELINE,
    SIGNAL_HISTORY_CAP,
)
from agents.langgraph.supervisor import (
    ROUTE_END,
    ROUTE_MEDIC,
    classify,
    route_after_supervisor,
    supervisor_node,
)

from .conftest import (
    confirmed_p99_history,
    degraded_endpoint_signals,
    healthy_signals,
    high_lag_signals,
    pipeline_failed_signals,
)

_CONFIG = HealingConfig()


def test_healthy_classifies_to_none():
    assert classify(healthy_signals(), _CONFIG) is None


def test_a_confirmed_p99_breach_is_an_incident():
    incident = classify(degraded_endpoint_signals(), _CONFIG, confirmed_p99_history(_CONFIG))
    assert incident["failure_class"] == FAILURE_ENDPOINT
    assert incident["fingerprint"] == "endpoint_latency:fraud-score"
    assert incident["detail"]["consecutive_breaches"] == _CONFIG.p99_confirmations_required


def test_a_single_p99_spike_is_not_an_incident():
    """One noisy sample must not trigger a model rollback.

    It used to: `classify` fired on the first reading above the threshold, and the Medic's
    response to a latency symptom is to archive the Production model and promote another.
    Latency and model correctness are unrelated — a cold start, a noisy neighbour or a
    network blip would have rolled back a perfectly good model, autonomously.
    """
    assert classify(degraded_endpoint_signals(), _CONFIG, []) is None


def test_an_intermittent_breach_never_confirms():
    """Breaches must be CONSECUTIVE; a flapping endpoint is not a persistent incident."""
    flapping = [degraded_endpoint_signals(), healthy_signals(), degraded_endpoint_signals()]
    assert classify(degraded_endpoint_signals(), _CONFIG, flapping) is None


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
    state = supervisor_node(
        {
            "signals": degraded_endpoint_signals(),
            "signal_history": confirmed_p99_history(_CONFIG),
            "decisions": [],
        },
        _CONFIG,
    )
    assert state["incident"]["failure_class"] == FAILURE_ENDPOINT
    assert state["decisions"][-1] == "supervisor: endpoint_latency"


def test_an_unconfirmed_breach_is_recorded_even_though_nothing_is_done():
    """Silence would make a debounced signal look identical to a healthy one in the audit."""
    state = supervisor_node(
        {"signals": degraded_endpoint_signals(), "signal_history": [], "decisions": []}, _CONFIG
    )
    assert state["incident"] is None
    assert "not confirmed" in state["decisions"][-1]


def test_the_supervisor_accumulates_signal_history():
    """The confirmation window needs history, and history needs somewhere to live."""
    state = supervisor_node(
        {"signals": healthy_signals(), "signal_history": [healthy_signals()], "decisions": []},
        _CONFIG,
    )
    assert len(state["signal_history"]) == 2


def test_history_is_bounded():
    state = supervisor_node(
        {
            "signals": healthy_signals(),
            "signal_history": [healthy_signals()] * (SIGNAL_HISTORY_CAP + 10),
            "decisions": [],
        },
        _CONFIG,
    )
    assert len(state["signal_history"]) == SIGNAL_HISTORY_CAP


def test_routing():
    assert route_after_supervisor({"incident": {"failure_class": FAILURE_ENDPOINT}}) == ROUTE_MEDIC
    assert route_after_supervisor({"incident": None}) == ROUTE_END
