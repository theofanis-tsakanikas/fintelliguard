"""Self-healing thresholds + tracing config (per docs/NARRATIVE.md, PROJECT_PLAN.md)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HealingConfig:
    """Thresholds and toggles for the Supervisor + Medic graph."""

    # Model Serving p99 latency above this (ms) -> roll back to the previous model version.
    p99_threshold_ms: float = 200.0
    # Kafka consumer lag above this (records) -> scale / alert.
    consumer_lag_threshold: int = 10_000
    # Retry a failed pipeline this many times before escalating.
    max_pipeline_retries: int = 1

    fraud_model_name: str = "fintelliguard.ml.fraud_scorer"

    # LangSmith tracing — OFF by default (no live calls in tests).
    enable_langsmith_tracing: bool = False
    langsmith_project: str = "fintelliguard-self-healing"
