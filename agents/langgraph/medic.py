"""Medic node: deterministic remediation for known failure classes (+ LLM root-cause).

Remediation collaborators are injected (`RemediationActions`) so the logic is testable
without touching the cloud:
  - endpoint p99 too high -> roll back to the previous model version (MLflow client)
  - consumer lag too high -> scale consumers / alert
  - pipeline failure     -> retry up to a bound, then escalate
  - unknown class        -> LLM root-cause (injectable, mocked) then escalate

Idempotency: a one-shot remediation is not repeated for an incident already acted on
(tracked by the incident fingerprint in `actions_taken`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agents.langgraph.config import HealingConfig
from agents.langgraph.state import (
    FAILURE_ENDPOINT,
    FAILURE_LAG,
    FAILURE_PIPELINE,
    HealthState,
)


@dataclass(frozen=True)
class RemediationActions:
    """Injectable remediation collaborators (cloud in prod, mocked in tests)."""

    mlflow: Any  # MlflowClient-like: search_model_versions + transition_model_version_stage
    restart_pipeline: Callable[[str], Any]
    scale_consumers: Callable[[str, int], Any]
    escalate: Callable[[str], Any]
    root_cause_llm: Callable[[dict[str, Any]], str]


def rollback_to_previous_model(mlflow_client: Any, model_name: str) -> dict[str, Any]:
    """Promote the previous model version back to Production (the serving fallback)."""
    versions = list(mlflow_client.search_model_versions(f"name='{model_name}'"))
    current = next((v for v in versions if v.current_stage == "Production"), None)
    candidates = [v for v in versions if v.current_stage != "Production"]
    if not candidates:
        return {"action": "rollback_unavailable", "model": model_name}

    previous = max(candidates, key=lambda v: int(v.version))
    mlflow_client.transition_model_version_stage(
        name=model_name,
        version=previous.version,
        stage="Production",
        archive_existing_versions=True,
    )
    return {
        "action": "rollback_model",
        "model": model_name,
        "from_version": current.version if current else None,
        "to_version": previous.version,
    }


def _has_action(state: HealthState, fingerprint: str, action: str | None = None) -> bool:
    for taken in state.get("actions_taken", []):
        if taken.get("fingerprint") == fingerprint and (
            action is None or taken.get("action") == action
        ):
            return True
    return False


def medic_node(
    state: HealthState, actions: RemediationActions, config: HealingConfig
) -> HealthState:
    """Remediate the classified incident."""
    incident = state["incident"]
    failure_class = incident["failure_class"]
    fingerprint = incident["fingerprint"]

    if failure_class == FAILURE_PIPELINE:
        return _handle_pipeline_failure(state, actions, config)

    # One-shot remediations are idempotent.
    if _has_action(state, fingerprint):
        decisions = [*state.get("decisions", []), f"medic: skip (already remediated {fingerprint})"]
        return {"decisions": decisions}

    if failure_class == FAILURE_ENDPOINT:
        result = rollback_to_previous_model(actions.mlflow, config.fraud_model_name)
    elif failure_class == FAILURE_LAG:
        result = _scale_consumers(actions, incident)
    else:
        result = _root_cause_and_escalate(actions, incident)

    result["fingerprint"] = fingerprint
    return {
        "actions_taken": [*state.get("actions_taken", []), result],
        "decisions": [*state.get("decisions", []), f"medic: {result['action']}"],
    }


def _scale_consumers(actions: RemediationActions, incident: dict[str, Any]) -> dict[str, Any]:
    topic = incident["detail"].get("topic", "?")
    target_instances = 2  # bump the consumer group; a real scaler would size from lag
    actions.scale_consumers(topic, target_instances)
    actions.escalate(f"consumer lag high on {topic}; scaled consumers to {target_instances}")
    return {"action": "scale_consumers", "topic": topic, "target_instances": target_instances}


def _root_cause_and_escalate(
    actions: RemediationActions, incident: dict[str, Any]
) -> dict[str, Any]:
    root_cause = actions.root_cause_llm(incident)
    actions.escalate(f"unknown incident; LLM root-cause: {root_cause}")
    return {"action": "root_cause_escalate", "root_cause": root_cause}


def _handle_pipeline_failure(
    state: HealthState, actions: RemediationActions, config: HealingConfig
) -> HealthState:
    incident = state["incident"]
    fingerprint = incident["fingerprint"]
    pipeline_id = incident["detail"].get("pipeline_id", "?")

    # Already escalated -> nothing more to do (idempotent terminal state).
    if _has_action(state, fingerprint, "escalate_pipeline"):
        decisions = [*state.get("decisions", []), f"medic: skip (already escalated {fingerprint})"]
        return {"decisions": decisions}

    retry_counts = dict(state.get("retry_counts", {}))
    attempts = retry_counts.get(fingerprint, 0)

    if attempts < config.max_pipeline_retries:
        actions.restart_pipeline(pipeline_id)
        retry_counts[fingerprint] = attempts + 1
        result = {"action": "retry_pipeline", "pipeline_id": pipeline_id, "attempt": attempts + 1}
    else:
        actions.escalate(f"pipeline {pipeline_id} still failing after {attempts} retr(y/ies)")
        result = {
            "action": "escalate_pipeline",
            "pipeline_id": pipeline_id,
            "after_retries": attempts,
        }

    result["fingerprint"] = fingerprint
    return {
        "actions_taken": [*state.get("actions_taken", []), result],
        "retry_counts": retry_counts,
        "decisions": [*state.get("decisions", []), f"medic: {result['action']}"],
    }
