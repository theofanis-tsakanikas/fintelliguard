"""Graph state + classification/failure-class constants.

The state carries collected health signals, the classified incident, an audit trail of
decisions, the actions already taken (to prevent double-remediation), and per-incident
retry counts.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

# Health classifications.
HEALTHY = "healthy"
DEGRADED = "degraded"
FAILED = "failed"

# Failure classes the Medic routes on.
FAILURE_PIPELINE = "pipeline_failure"
FAILURE_ENDPOINT = "endpoint_latency"
FAILURE_LAG = "consumer_lag"
FAILURE_UNKNOWN = "unknown"


class HealthState(TypedDict, total=False):
    """LangGraph state for the self-healing run."""

    signals: dict[str, Any]
    # Optional (not `| None`): LangGraph get_type_hints-evaluates these at runtime, and
    # PEP 604 unions on a subscripted generic fail under Python 3.9.
    incident: Optional[dict[str, Any]]  # noqa: UP045
    decisions: list[str]
    actions_taken: list[dict[str, Any]]
    retry_counts: dict[str, int]


def initial_state() -> HealthState:
    """A clean starting state."""
    return {
        "signals": {},
        "incident": None,
        "decisions": [],
        "actions_taken": [],
        "retry_counts": {},
    }
