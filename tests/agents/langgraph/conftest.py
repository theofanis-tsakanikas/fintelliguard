"""Shared fakes for the self-healing tests: mocked monitors, MLflow, and actions."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.langgraph.medic import RemediationActions


class _ModelVersion:
    def __init__(self, version: str, stage: str, run_id: str | None = None):
        self.version = version
        self.current_stage = stage
        self.run_id = run_id or f"run-{version}"


class _Run:
    def __init__(self, metrics: dict):
        self.data = type("Data", (), {"metrics": metrics})()


# A version that passes the promotion gate, and one that does not.
PASSING_METRICS = {"auc_roc": 0.94, "fraud_precision": 0.88}
FAILING_METRICS = {"auc_roc": 0.71, "fraud_precision": 0.60}


class FakeMlflowClient:
    """A realistic registry — deliberately including the shape that hid the rollback bug.

    The old fixture held exactly v1(Archived) and v2(Production), so
    `max(non_production_versions)` happened to pick v1 and the test went green. Add a
    STAGING version that failed its gate and the old code picks THAT — promoting an
    unvalidated model into the payment path and archiving the good one. The registry a real
    project has always contains a Staging candidate; the fixture that omitted it was the
    reason nobody looked.
    """

    def __init__(self):
        self.versions = [
            _ModelVersion("1", "Archived"),  # older good model — the true fallback
            _ModelVersion("2", "Production"),  # currently serving
            _ModelVersion("3", "Staging"),  # newest, UNVALIDATED — never a rollback target
        ]
        self.runs = {
            "run-1": _Run(dict(PASSING_METRICS)),
            "run-2": _Run(dict(PASSING_METRICS)),
            "run-3": _Run(dict(FAILING_METRICS)),  # why v3 is still in Staging
        }
        self.transitions = []

    def search_model_versions(self, filter_string):
        return list(self.versions)

    def get_run(self, run_id):
        return self.runs[run_id]

    def transition_model_version_stage(
        self, *, name, version, stage, archive_existing_versions=False
    ):
        self.transitions.append(
            {"name": name, "version": version, "stage": stage, "archive": archive_existing_versions}
        )


class Recorder:
    """Records calls to an injected action."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


@dataclass
class StubMonitors:
    """A monitors object whose collect() returns a fixed signals dict."""

    signals: dict

    def collect(self) -> dict:
        return self.signals


@pytest.fixture
def mlflow_client():
    return FakeMlflowClient()


@pytest.fixture
def actions(mlflow_client):
    return RemediationActions(
        mlflow=mlflow_client,
        restart_pipeline=Recorder(),
        scale_consumers=Recorder(),
        escalate=Recorder(),
        root_cause_llm=lambda incident: "mocked root cause",
    )


def healthy_signals():
    return {
        "pipeline_health": {"pipeline_id": "pl-1", "state": "RUNNING"},
        "consumer_lag": {"topic": "txn.raw", "lag_records": 12},
        "endpoint_p99": {"endpoint": "fraud-score", "p99_ms": 35.0},
    }


def degraded_endpoint_signals():
    s = healthy_signals()
    s["endpoint_p99"] = {"endpoint": "fraud-score", "p99_ms": 350.0}
    return s


def confirmed_p99_history(config=None):
    """Enough consecutive breaching samples that a p99 incident is confirmed.

    A single sample used to fire a model rollback. Latency is noisy and model correctness
    is unrelated to it, so one blip from a cold start could archive the Production model.
    Tests that expect a rollback must now supply a breach that PERSISTED.
    """
    from agents.langgraph.config import HealingConfig

    config = config or HealingConfig()
    # -1: the current sample is the last confirmation.
    return [degraded_endpoint_signals() for _ in range(config.p99_confirmations_required - 1)]


def high_lag_signals():
    s = healthy_signals()
    s["consumer_lag"] = {"topic": "txn.raw", "lag_records": 250_000}
    return s


def pipeline_failed_signals():
    s = healthy_signals()
    s["pipeline_health"] = {"pipeline_id": "pl-1", "state": "FAILED"}
    return s
