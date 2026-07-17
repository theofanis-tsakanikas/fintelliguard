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
    OUTCOME_HEALTHY,
    SIGNAL_HISTORY_CAP,
    HealthState,
)


def _p99_breaches(sample: dict[str, Any], config: HealingConfig) -> bool:
    return sample.get("endpoint_p99", {}).get("p99_ms", 0.0) > config.p99_threshold_ms


def confirmed_p99_breach(
    signals: dict[str, Any], history: list[dict[str, Any]], config: HealingConfig
) -> tuple[bool, int]:
    """Has p99 breached on enough CONSECUTIVE samples to act on?

    Returns `(confirmed, consecutive_count)`.

    A single sample above the threshold used to trigger a model rollback. Latency is a
    noisy continuous quantity and model correctness is unrelated to it; one blip from a
    cold start or a noisy neighbour was enough to archive the Production model. Requiring
    the breach to persist is the difference between a symptom and an incident.

    This is why the checkpointer matters. `classify` is stateless, so the confirmation
    window needs `signal_history` to survive between cycles — the durability gap and the
    debounce gap are the same gap, and fixing state is the precondition for arming this
    trigger at all.
    """
    if not _p99_breaches(signals, config):
        return False, 0

    consecutive = 1  # the current sample
    for sample in reversed(history):
        if not _p99_breaches(sample, config):
            break
        consecutive += 1

    return consecutive >= config.p99_confirmations_required, consecutive


def classify(
    signals: dict[str, Any],
    config: HealingConfig,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Classify the most severe incident, or None when healthy.

    Priority: a failed pipeline outranks endpoint latency, which outranks consumer lag.
    The `fingerprint` identifies the incident for idempotent remediation.

    `history` is the previous signal samples, needed only for the p99 confirmation window.
    """
    history = history or []
    pipeline = signals.get("pipeline_health", {})
    endpoint = signals.get("endpoint_p99", {})
    lag = signals.get("consumer_lag", {})

    if pipeline.get("state") == "FAILED":
        # A pipeline reporting FAILED is a discrete fact, already confirmed by the platform
        # that reported it. It needs no window; a latency reading does.
        pipeline_id = pipeline.get("pipeline_id", "?")
        return {
            "classification": FAILED,
            "failure_class": FAILURE_PIPELINE,
            "detail": pipeline,
            "fingerprint": f"{FAILURE_PIPELINE}:{pipeline_id}",
        }

    confirmed, consecutive = confirmed_p99_breach(signals, history, config)
    if confirmed:
        return {
            "classification": DEGRADED,
            "failure_class": FAILURE_ENDPOINT,
            "detail": {**endpoint, "consecutive_breaches": consecutive},
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
    signals = state.get("signals", {})
    history = list(state.get("signal_history", []))
    incident = classify(signals, config, history)

    label = incident["failure_class"] if incident else "healthy"
    decisions = [*state.get("decisions", []), f"supervisor: {label}"]

    # An unconfirmed p99 breach is worth SAYING, not acting on. Silence here would make a
    # debounced signal indistinguishable from a healthy one in the audit trail.
    breaching, consecutive = _p99_breaches(signals, config), 0
    if breaching and not incident:
        _, consecutive = confirmed_p99_breach(signals, history, config)
        decisions.append(
            f"supervisor: p99 breach {consecutive}/{config.p99_confirmations_required} "
            "— not confirmed, taking no action"
        )

    update: HealthState = {
        "incident": incident,
        "decisions": decisions,
        "signal_history": [*history, signals][-SIGNAL_HISTORY_CAP:],
    }
    if incident is None:
        update["outcome"] = OUTCOME_HEALTHY
    return update


# Routing keys for the conditional edge after the supervisor.
ROUTE_MEDIC = "medic"
ROUTE_END = "end"


def route_after_supervisor(state: HealthState) -> str:
    """Send to the Medic when there is an incident, otherwise end the graph."""
    return ROUTE_MEDIC if state.get("incident") else ROUTE_END
