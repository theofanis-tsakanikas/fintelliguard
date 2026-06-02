"""Supervisor node: assess signals, classify an incident, route to Medic or end."""

from __future__ import annotations

from typing import Any

from agents.langgraph.config import HealingConfig
from agents.langgraph.state import (
    DEGRADED,
    FAILED,
    FAILURE_ENDPOINT,
    FAILURE_LAG,
    FAILURE_PIPELINE,
    HealthState,
)


def classify(signals: dict[str, Any], config: HealingConfig) -> dict[str, Any] | None:
    """Classify the most severe incident, or None when healthy.

    Priority: a failed pipeline outranks endpoint latency, which outranks consumer lag.
    The `fingerprint` identifies the incident for idempotent remediation.
    """
    pipeline = signals.get("pipeline_health", {})
    endpoint = signals.get("endpoint_p99", {})
    lag = signals.get("consumer_lag", {})

    if pipeline.get("state") == "FAILED":
        pipeline_id = pipeline.get("pipeline_id", "?")
        return {
            "classification": FAILED,
            "failure_class": FAILURE_PIPELINE,
            "detail": pipeline,
            "fingerprint": f"{FAILURE_PIPELINE}:{pipeline_id}",
        }

    if endpoint.get("p99_ms", 0.0) > config.p99_threshold_ms:
        return {
            "classification": DEGRADED,
            "failure_class": FAILURE_ENDPOINT,
            "detail": endpoint,
            "fingerprint": f"{FAILURE_ENDPOINT}:{endpoint.get('endpoint', '?')}",
        }

    if lag.get("lag_records", 0) > config.consumer_lag_threshold:
        return {
            "classification": DEGRADED,
            "failure_class": FAILURE_LAG,
            "detail": lag,
            "fingerprint": f"{FAILURE_LAG}:{lag.get('topic', '?')}",
        }

    return None


def supervisor_node(state: HealthState, config: HealingConfig) -> HealthState:
    """Assess the collected signals and record the classified incident."""
    incident = classify(state.get("signals", {}), config)
    label = incident["failure_class"] if incident else "healthy"
    decisions = [*state.get("decisions", []), f"supervisor: {label}"]
    return {"incident": incident, "decisions": decisions}


# Routing keys for the conditional edge after the supervisor.
ROUTE_MEDIC = "medic"
ROUTE_END = "end"


def route_after_supervisor(state: HealthState) -> str:
    """Send to the Medic when there is an incident, otherwise end the graph."""
    return ROUTE_MEDIC if state.get("incident") else ROUTE_END
