"""Medic remediation per failure class + idempotency (no graph)."""

from __future__ import annotations

from agents.langgraph.config import HealingConfig
from agents.langgraph.medic import medic_node, rollback_to_previous_model
from agents.langgraph.state import (
    FAILURE_ENDPOINT,
    FAILURE_LAG,
    FAILURE_PIPELINE,
    FAILURE_UNKNOWN,
)

from .conftest import FAILING_METRICS, _ModelVersion, _Run

_CONFIG = HealingConfig()


def _state(failure_class, fingerprint, detail=None, **extra):
    base = {
        "incident": {
            "failure_class": failure_class,
            "fingerprint": fingerprint,
            "detail": detail or {},
        },
        "decisions": [],
        "actions_taken": [],
        "retry_counts": {},
    }
    base.update(extra)
    return base


def test_rollback_promotes_previous_version(mlflow_client):
    result = rollback_to_previous_model(mlflow_client, "fintelliguard.ml.fraud_scorer")
    assert result["action"] == "rollback_model"
    assert result["to_version"] == "1"  # v1 (previous) promoted; v2 was Production
    assert mlflow_client.transitions == [
        {
            "name": "fintelliguard.ml.fraud_scorer",
            "version": "1",
            "stage": "Production",
            "archive": True,
        }
    ]


def test_endpoint_latency_triggers_rollback(actions, mlflow_client):
    state = _state(FAILURE_ENDPOINT, "endpoint_latency:fraud-score", {"endpoint": "fraud-score"})
    out = medic_node(state, actions, _CONFIG)
    assert out["actions_taken"][-1]["action"] == "rollback_model"
    assert len(mlflow_client.transitions) == 1


def test_consumer_lag_scales_and_alerts(actions):
    state = _state(FAILURE_LAG, "consumer_lag:txn.raw", {"topic": "txn.raw"})
    out = medic_node(state, actions, _CONFIG)
    assert out["actions_taken"][-1]["action"] == "scale_consumers"
    assert actions.scale_consumers.calls == [("txn.raw", 2)]
    assert actions.escalate.calls  # alerted


def test_pipeline_failure_retries_then_escalates(actions):
    detail = {"pipeline_id": "pl-1"}

    retry = medic_node(_state(FAILURE_PIPELINE, "pipeline_failure:pl-1", detail), actions, _CONFIG)
    assert retry["actions_taken"][-1]["action"] == "retry_pipeline"
    assert actions.restart_pipeline.calls == [("pl-1",)]

    # Already retried up to the bound -> escalate.
    seeded = _state(
        FAILURE_PIPELINE, "pipeline_failure:pl-1", detail, retry_counts={"pipeline_failure:pl-1": 1}
    )
    escalated = medic_node(seeded, actions, _CONFIG)
    assert escalated["actions_taken"][-1]["action"] == "escalate_pipeline"
    assert actions.escalate.calls


def test_unknown_class_uses_llm_root_cause(actions):
    state = _state(FAILURE_UNKNOWN, "unknown:mystery", {"foo": "bar"})
    out = medic_node(state, actions, _CONFIG)
    result = out["actions_taken"][-1]
    assert result["action"] == "root_cause_escalate"
    assert result["root_cause"] == "mocked root cause"
    assert actions.escalate.calls


def test_idempotent_one_shot_remediation(actions, mlflow_client):
    fingerprint = "endpoint_latency:fraud-score"
    already = _state(
        FAILURE_ENDPOINT,
        fingerprint,
        {"endpoint": "fraud-score"},
        actions_taken=[{"action": "rollback_model", "fingerprint": fingerprint}],
    )
    out = medic_node(already, actions, _CONFIG)
    # No second rollback.
    assert mlflow_client.transitions == []
    assert "skip" in out["decisions"][-1]


# --------------------------------------------------------------------------- #
# Rollback safety — the most dangerous bug in the repository
# --------------------------------------------------------------------------- #


def test_rollback_never_promotes_a_staging_version(mlflow_client):
    """An unvalidated model must never reach Production, least of all autonomously.

    The bug:

        candidates = [v for v in versions if v.current_stage != "Production"]
        previous = max(candidates, key=lambda v: int(v.version))

    `candidates` includes Staging. `max` takes the highest VERSION NUMBER, not the previous
    Production one. So with v2 serving and v3 sitting in Staging having failed the
    AUC >= 0.92 gate, a p99 blip promoted **v3** — archiving the good model on the way past.
    An untested fraud model decides real payments, no human involved, in direct violation of
    the promotion policy this project calls non-negotiable.

    The old test passed only because its fixture held exactly v1(Archived)/v2(Production).
    The fixture now contains the Staging version a real registry always has.
    """
    result = rollback_to_previous_model(mlflow_client, "m")

    assert result["action"] == "rollback_model"
    assert result["to_version"] == "1", (
        f"rolled back to v{result['to_version']} — v3 is in STAGING and has never served "
        "traffic; the only valid fallback is the version that was previously Production"
    )
    assert mlflow_client.transitions[0]["version"] == "1"


def test_rollback_applies_the_same_promotion_gate_as_a_forward_promotion(mlflow_client):
    """A rollback IS a promotion. The agent is not an exception to the policy."""
    # The only archived candidate now fails its gate.
    mlflow_client.runs["run-1"] = _Run(dict(FAILING_METRICS))

    result = rollback_to_previous_model(mlflow_client, "m")

    assert result["action"] == "rollback_refused"
    assert "promotion gate" in result["reason"]
    assert mlflow_client.transitions == [], "a model that fails the gate was promoted anyway"


def test_rollback_fails_closed_when_metrics_cannot_be_read(mlflow_client):
    """'We could not check' is not a reason to put a model in front of payments."""

    def _boom(run_id):
        raise RuntimeError("tracking server unreachable")

    mlflow_client.get_run = _boom
    result = rollback_to_previous_model(mlflow_client, "m")

    assert result["action"] == "rollback_refused"
    assert mlflow_client.transitions == []


def test_rollback_is_unavailable_when_nothing_was_ever_promoted(mlflow_client):
    """With no previously-Production version there is no fallback — and that is not an
    invitation to promote whatever happens to be newest."""
    mlflow_client.versions = [
        _ModelVersion("1", "Production"),
        _ModelVersion("2", "Staging"),
    ]
    result = rollback_to_previous_model(mlflow_client, "m")

    assert result["action"] == "rollback_unavailable"
    assert mlflow_client.transitions == []


def test_a_refused_rollback_pages_a_human(mlflow_client, actions):
    """An agent that gives up SILENTLY is worse than one that does nothing.

    `rollback_refused` and `rollback_unavailable` never called `escalate`. So: the endpoint
    is degraded, the only fallback fails its gate, the Medic declines to act — correctly —
    and nobody is told. The p99 breach stays, the model stays, and the run reports itself as
    handled.
    """
    from agents.langgraph.config import HealingConfig
    from agents.langgraph.medic import medic_node
    from agents.langgraph.state import FAILURE_ENDPOINT, OUTCOME_REFUSED

    mlflow_client.runs["run-1"] = _Run(dict(FAILING_METRICS))  # the only fallback is unfit
    state = {
        "incident": {
            "failure_class": FAILURE_ENDPOINT,
            "fingerprint": "endpoint_latency:fraud-score",
            "detail": {"endpoint": "fraud-score", "p99_ms": 900.0},
        },
        "actions_taken": [],
        "decisions": [],
        "total_actions": 0,
    }
    result = medic_node(state, actions, HealingConfig())

    assert result["actions_taken"][-1]["action"] == "rollback_refused"
    assert result["outcome"] == OUTCOME_REFUSED
    assert actions.escalate.calls, "the agent refused to act and told nobody"


def test_a_refusal_is_not_later_reported_as_a_remediation(mlflow_client, actions):
    """The idempotency short-circuit returned OUTCOME_REMEDIATED unconditionally.

    So a refused rollback reported "remediated" on every subsequent cycle, forever, while
    the endpoint stayed broken — the layer reporting the wrong one of the three states the
    `outcome` field exists to distinguish, on the safety path.
    """
    from agents.langgraph.config import HealingConfig
    from agents.langgraph.medic import medic_node
    from agents.langgraph.state import FAILURE_ENDPOINT, OUTCOME_REFUSED

    mlflow_client.runs["run-1"] = _Run(dict(FAILING_METRICS))
    incident = {
        "failure_class": FAILURE_ENDPOINT,
        "fingerprint": "endpoint_latency:fraud-score",
        "detail": {"endpoint": "fraud-score", "p99_ms": 900.0},
    }
    first = medic_node(
        {"incident": incident, "actions_taken": [], "decisions": [], "total_actions": 0},
        actions,
        HealingConfig(),
    )
    second = medic_node(
        {
            "incident": incident,
            "actions_taken": first["actions_taken"],
            "decisions": first["decisions"],
            "total_actions": first["total_actions"],
        },
        actions,
        HealingConfig(),
    )
    assert second["outcome"] == OUTCOME_REFUSED, (
        f"a refused rollback reports {second['outcome']!r} on the next cycle while the "
        "endpoint is still broken"
    )
