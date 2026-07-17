"""Graph state + classification/failure-class constants.

The state carries collected health signals, a bounded history of those signals, the
classified incident, an audit trail of decisions, the actions already taken (to prevent
double-remediation), per-incident retry counts, and the run's terminal outcome.
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

# Terminal outcomes. There is exactly one success value, and it is not the default.
#
# There was no outcome field at all: a caller inferred success by reading `actions_taken`,
# and the tests literally do that. So a scheduler could not distinguish "healthy, nothing
# to do" from "acted and remediated" from "gave up, a human must look" — three states with
# very different meanings for a regulated payment system. Reaching the end of the graph is
# not success by itself.
OUTCOME_UNKNOWN = "unknown"  # fail-closed default: nothing has claimed otherwise
OUTCOME_HEALTHY = "healthy"  # no incident; the normal case for a daemon
OUTCOME_REMEDIATED = "remediated"  # an action was taken and it was allowed to be taken
OUTCOME_ESCALATED = "escalated"  # a human is needed
OUTCOME_REFUSED = "refused"  # an action was wanted but a safety bound forbade it

# How many signal samples to keep. Enough to satisfy any confirmation window, small enough
# to keep the checkpointed state cheap.
SIGNAL_HISTORY_CAP = 20


class HealthState(TypedDict, total=False):
    """LangGraph state for the self-healing run."""

    signals: dict[str, Any]
    # The last few signal samples, oldest first. Required for confirmation windows:
    # `classify` is stateless, so without history it cannot tell a persistent p99 breach
    # from one noisy reading — and a single sample used to trigger a model rollback.
    signal_history: list[dict[str, Any]]
    # Optional (not `| None`): LangGraph get_type_hints-evaluates these at runtime, and
    # PEP 604 unions on a subscripted generic fail under Python 3.9.
    incident: Optional[dict[str, Any]]  # noqa: UP045
    decisions: list[str]
    actions_taken: list[dict[str, Any]]
    retry_counts: dict[str, int]
    # Total remediation actions this thread has taken, ever. The per-fingerprint retry
    # counter bounds ONE incident; nothing bounded the total, so N failing pipelines each
    # got their own budget and the blast radius was unbounded.
    total_actions: int
    outcome: str


def initial_state() -> HealthState:
    """A clean starting state."""
    return {
        "signals": {},
        "signal_history": [],
        "incident": None,
        "decisions": [],
        "actions_taken": [],
        "retry_counts": {},
        "total_actions": 0,
        "outcome": OUTCOME_UNKNOWN,
    }
