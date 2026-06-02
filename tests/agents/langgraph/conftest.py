"""Shared fakes for the self-healing tests: mocked monitors, MLflow, and actions."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.langgraph.medic import RemediationActions


class _ModelVersion:
    def __init__(self, version: str, stage: str):
        self.version = version
        self.current_stage = stage


class FakeMlflowClient:
    """Two versions: v2 in Production, v1 archived (the rollback target)."""

    def __init__(self):
        self.versions = [_ModelVersion("1", "Archived"), _ModelVersion("2", "Production")]
        self.transitions = []

    def search_model_versions(self, filter_string):
        return list(self.versions)

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


def high_lag_signals():
    s = healthy_signals()
    s["consumer_lag"] = {"topic": "txn.raw", "lag_records": 250_000}
    return s


def pipeline_failed_signals():
    s = healthy_signals()
    s["pipeline_health"] = {"pipeline_id": "pl-1", "state": "FAILED"}
    return s
