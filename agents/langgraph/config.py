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

    # Consecutive samples that must breach p99 before the Medic acts.
    #
    # A single reading used to fire a MODEL ROLLBACK. Latency and model correctness are
    # unrelated quantities: a noisy neighbour, a cold start or a network blip would roll
    # back a perfectly good model. A CI job's `conclusion: failure` is a discrete, already-
    # confirmed fact; a p99 reading is a noisy continuous sample and must be confirmed
    # before anything irreversible happens because of it.
    p99_confirmations_required: int = 3

    # Hard ceiling on remediation actions per healing thread. The per-fingerprint retry
    # counter bounds a SINGLE incident; nothing bounded the total, so N distinct failing
    # pipelines each received their own budget with no cap across them. This is the
    # cheapest possible blast-radius limit: past it, the agent escalates instead of acting.
    max_total_actions: int = 5

    fraud_model_name: str = "fintelliguard.ml.fraud_scorer"

    # Durable state, ON by default.
    #
    # The graph compiled with no checkpointer and `run_self_healing` defaulted to
    # `initial_state()`, so every cycle started with `retry_counts={}` — attempts always 0,
    # always below the retry bound, `restart_pipeline` forever, escalation unreachable, no
    # human ever paged. The tests passed only because they threaded state by hand, which
    # production had no way to do.
    #
    # Off is not offered as a convenience: without it the retry bound and the confirmation
    # window are both decorative.
    enable_checkpointing: bool = True
    # STABLE by design. A per-run thread id makes a checkpointer decorative — each cycle
    # would start fresh and reset exactly what durability is for.
    healing_thread_id: str = "fintelliguard-self-healing"

    # LangSmith tracing — OFF by default (no live calls in tests).
    enable_langsmith_tracing: bool = False
    langsmith_project: str = "fintelliguard-self-healing"
