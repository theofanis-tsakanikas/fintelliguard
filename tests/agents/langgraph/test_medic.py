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
