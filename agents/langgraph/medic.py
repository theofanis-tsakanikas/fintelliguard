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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agents.langgraph.config import HealingConfig
from agents.langgraph.state import (
    FAILURE_ENDPOINT,
    FAILURE_LAG,
    FAILURE_PIPELINE,
    OUTCOME_ESCALATED,
    OUTCOME_REFUSED,
    OUTCOME_REMEDIATED,
    OUTCOME_UNKNOWN,
    HealthState,
)
from ml.training.promote import PromotionDecision, evaluate_promotion


@dataclass(frozen=True)
class RemediationActions:
    """Injectable remediation collaborators (cloud in prod, mocked in tests)."""

    mlflow: Any  # MlflowClient-like: search_model_versions + transition_model_version_stage
    restart_pipeline: Callable[[str], Any]
    scale_consumers: Callable[[str, int], Any]
    escalate: Callable[[str], Any]
    root_cause_llm: Callable[[dict[str, Any]], str]


def rollback_to_previous_model(mlflow_client: Any, model_name: str) -> dict[str, Any]:
    """Restore the last model version that was ITSELF in Production.

    The bug this replaces was the most dangerous in the repository:

        candidates = [v for v in versions if v.current_stage != "Production"]
        previous = max(candidates, key=lambda v: int(v.version))

    `candidates` is every non-Production version — Staging, None and Archived alike — and
    `max` picks the HIGHEST VERSION NUMBER, not the previous Production one. So: v5 is
    serving, v6 sits in Staging having failed the AUC >= 0.92 gate, endpoint p99 crosses
    200ms, and the Medic autonomously promotes **v6** to Production with
    `archive_existing_versions=True`, archiving the good model on its way past. An untested
    fraud model decides real payments, with no human involved, in direct violation of the
    promotion policy this project states as non-negotiable.

    The test passed because its fixture happened to contain exactly v1(None)/v2(Production).
    Add a v3 in Staging and it would have picked v3 — a case the test never explored.

    Two rules now:

    * **Archived only.** `archive_existing_versions=True` is what put the previous
      Production model into Archived, so Archived-with-a-lower-version IS the rollback
      target. Staging has never served traffic and is not a fallback; it is a candidate
      awaiting a gate.
    * **The promotion gate still applies.** A rollback is a promotion. The same
      AUC >= 0.92 AND precision >= 0.85 that guards a forward promotion guards this one,
      read from the version's own run. An agent is not an exception to the policy.
    """
    versions = list(mlflow_client.search_model_versions(f"name='{model_name}'"))
    current = next((v for v in versions if v.current_stage == "Production"), None)

    # Only versions that were previously promoted and then archived. Never Staging, never
    # None: those have not passed the gate, and one of them being newer is not a reason to
    # put it in front of payment traffic.
    archived = [v for v in versions if v.current_stage == "Archived"]
    if current is not None:
        archived = [v for v in archived if int(v.version) < int(current.version)]

    if not archived:
        return {
            "action": "rollback_unavailable",
            "model": model_name,
            "reason": "no previously-promoted version to fall back to",
        }

    previous = max(archived, key=lambda v: int(v.version))

    decision = _promotion_decision(mlflow_client, previous)
    if not decision.promote:
        return {
            "action": "rollback_refused",
            "model": model_name,
            "candidate_version": previous.version,
            "reason": f"candidate fails the promotion gate: {decision.reason}",
        }

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
        "gate": decision.reason,
    }


def _promotion_decision(mlflow_client: Any, version: Any) -> PromotionDecision:
    """The promotion gate, applied to a rollback candidate's own recorded metrics.

    Fails closed: a version whose run cannot be read has not demonstrated anything, and
    "we could not check" is not a reason to put a model in front of payments.
    """
    run_id = getattr(version, "run_id", None)
    if not run_id:
        return PromotionDecision(False, "version has no run to read metrics from")
    try:
        run = mlflow_client.get_run(run_id)
        metrics = dict(run.data.metrics)
    except Exception as exc:  # noqa: BLE001 - any failure to read metrics is a refusal
        return PromotionDecision(False, f"could not read metrics for run {run_id}: {exc}")
    return evaluate_promotion(metrics)


def _has_action(state: HealthState, fingerprint: str, action: str | None = None) -> bool:
    for taken in state.get("actions_taken", []):
        if taken.get("fingerprint") == fingerprint and (
            action is None or taken.get("action") == action
        ):
            return True
    return False


def _budget_exhausted(
    state: HealthState, actions: RemediationActions, config: HealingConfig
) -> HealthState | None:
    """Refuse to act once this thread has spent its action budget.

    The blast-radius cap the layer never had. `retry_counts[fingerprint]` bounds ONE
    incident; nothing bounded the total, so ten distinct failing pipelines each got their
    own budget and the agent could act indefinitely across them. Past the ceiling it stops
    and pages a human, which is the correct behaviour for an agent that has already tried
    several things and is still looking at a broken system.
    """
    spent = state.get("total_actions", 0)
    if spent < config.max_total_actions:
        return None

    actions.escalate(
        f"remediation budget exhausted: {spent} actions taken this run "
        f"(limit {config.max_total_actions}); a human must look"
    )
    return {
        "decisions": [
            *state.get("decisions", []),
            f"medic: refused — action budget {spent}/{config.max_total_actions} exhausted",
        ],
        "outcome": OUTCOME_ESCALATED,
    }


def medic_node(
    state: HealthState, actions: RemediationActions, config: HealingConfig
) -> HealthState:
    """Remediate the classified incident."""
    incident = state["incident"]
    failure_class = incident["failure_class"]
    fingerprint = incident["fingerprint"]

    if refusal := _budget_exhausted(state, actions, config):
        return refusal

    if failure_class == FAILURE_PIPELINE:
        return _handle_pipeline_failure(state, actions, config)

    # One-shot remediations are idempotent — but "already acted" must report what that
    # action ACHIEVED, not assume it worked. This returned OUTCOME_REMEDIATED
    # unconditionally, so a refused rollback reported "remediated" on every subsequent
    # cycle, forever, while the endpoint stayed broken — the layer reporting the wrong one
    # of the three states the `outcome` field was introduced to distinguish, on the safety
    # path.
    if _has_action(state, fingerprint):
        prior = _outcome_of_prior_action(state, fingerprint)
        decisions = [
            *state.get("decisions", []),
            f"medic: skip (already acted on {fingerprint}: {prior})",
        ]
        return {"decisions": decisions, "outcome": prior}

    if failure_class == FAILURE_ENDPOINT:
        result = rollback_to_previous_model(actions.mlflow, config.fraud_model_name)
        # A refusal is the agent saying "I cannot fix this" while the endpoint is still
        # broken. It used to say it to nobody: `rollback_refused` / `rollback_unavailable`
        # never called `escalate`, so the p99 breach stayed, the model stayed, and no human
        # was told. An agent that gives up silently is worse than one that does nothing.
        if result["action"] in _REFUSALS:
            actions.escalate(
                f"endpoint degraded and rollback refused: {result.get('reason', '?')} — "
                "a human must decide"
            )
    elif failure_class == FAILURE_LAG:
        result = _scale_consumers(actions, incident)
    else:
        result = _root_cause_and_escalate(actions, incident)

    result["fingerprint"] = fingerprint
    return {
        "actions_taken": [*state.get("actions_taken", []), result],
        "total_actions": state.get("total_actions", 0) + 1,
        "decisions": [*state.get("decisions", []), f"medic: {result['action']}"],
        "outcome": _outcome_for(result["action"]),
    }


# What each action means for the run's terminal state. A rollback the gate refused is NOT
# a remediation, and reporting it as one is how an agent's failure becomes invisible.
_REFUSALS = frozenset({"rollback_refused", "rollback_unavailable"})
_ESCALATIONS = frozenset({"root_cause_escalate", "escalate_pipeline"})


def _outcome_of_prior_action(state: HealthState, fingerprint: str) -> str:
    """What the action already taken for this incident actually achieved."""
    for taken in reversed(state.get("actions_taken", [])):
        if taken.get("fingerprint") == fingerprint:
            return _outcome_for(str(taken.get("action", "")))
    return OUTCOME_UNKNOWN


def _outcome_for(action: str) -> str:
    if action in _REFUSALS:
        return OUTCOME_REFUSED
    if action in _ESCALATIONS:
        return OUTCOME_ESCALATED
    return OUTCOME_REMEDIATED


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
        return {"decisions": decisions, "outcome": OUTCOME_ESCALATED}

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
        "total_actions": state.get("total_actions", 0) + 1,
        "decisions": [*state.get("decisions", []), f"medic: {result['action']}"],
        "outcome": _outcome_for(result["action"]),
    }
