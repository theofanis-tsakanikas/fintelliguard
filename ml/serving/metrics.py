"""Prometheus exporter for the local fraud-scoring service.

Emits the EXACT metric names the committed Grafana dashboards query
(`dashboards/serving_latency.json`): `model_serving_request_duration_seconds`,
`model_serving_requests_total`, `model_serving_build_info` — turning the repo's
Prometheus/Grafana story from config-and-dashboard-JSON-only into a real, scrapeable
`/metrics` endpoint. Plus fraud-specific series (score distribution, verdict-gate outcomes,
guardrail blocks, quarantines) so the local dashboard shows the whole funnel.

Everything is labelled `endpoint="fintelliguard-fraud-score"` + `environment`, matching the
dashboard PromQL selectors. Pass a private ``registry`` in tests to avoid the global-registry
duplicate-timeseries error.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

ENDPOINT = "fintelliguard-fraud-score"

# Latency buckets tuned for a sub-50ms scorer (the Tier-1 SLA).
_LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
_SCORE_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


class ServingMetrics:
    """The scorer's Prometheus instruments, named to match the Grafana dashboards."""

    def __init__(
        self,
        *,
        environment: str = "local",
        model_version: str = "fraud-xgb:local",
        registry: CollectorRegistry | None = None,
    ) -> None:
        self.environment = environment
        common = {"registry": registry} if registry is not None else {}

        self._duration = Histogram(
            "model_serving_request_duration_seconds",
            "Fraud-scoring request latency in seconds.",
            ["endpoint", "environment"],
            buckets=_LATENCY_BUCKETS,
            **common,
        )
        self._requests = Counter(
            "model_serving_requests",
            "Fraud-scoring requests, by decision.",
            ["endpoint", "environment", "decision"],
            **common,
        )
        self._build = Gauge(
            "model_serving_build_info",
            "Model build info (always 1; carries the model_version label).",
            ["endpoint", "environment", "model_version"],
            **common,
        )
        self._score = Histogram(
            "fintelliguard_fraud_score",
            "Distribution of fraud scores.",
            ["endpoint", "environment"],
            buckets=_SCORE_BUCKETS,
            **common,
        )
        self._verdict = Counter(
            "fintelliguard_verdict_gate",
            "Tier-2 verdict-acceptance-gate outcomes.",
            ["endpoint", "environment", "result"],
            **common,
        )
        self._guardrail = Counter(
            "fintelliguard_guardrail_blocks",
            "Output-guardrail blocks, by policy class.",
            ["endpoint", "environment", "policy"],
            **common,
        )
        self._quarantined = Counter(
            "fintelliguard_quarantined",
            "Transactions quarantined (could not be turned into features).",
            ["endpoint", "environment"],
            **common,
        )
        self._build.labels(ENDPOINT, environment, model_version).set(1)

    def observe_request(self, duration_seconds: float, decision: str) -> None:
        self._duration.labels(ENDPOINT, self.environment).observe(duration_seconds)
        self._requests.labels(ENDPOINT, self.environment, decision).inc()

    def observe_score(self, score: float) -> None:
        self._score.labels(ENDPOINT, self.environment).observe(score)

    def record_verdict(self, accepted: bool) -> None:
        self._verdict.labels(
            ENDPOINT, self.environment, "accepted" if accepted else "rejected"
        ).inc()

    def record_guardrail_block(self, policy: str | None) -> None:
        self._guardrail.labels(ENDPOINT, self.environment, policy or "unknown").inc()

    def record_quarantine(self) -> None:
        self._quarantined.labels(ENDPOINT, self.environment).inc()


def serve_metrics(port: int = 8000) -> None:
    """Start the Prometheus HTTP endpoint (`/metrics`) on ``port`` (non-blocking)."""
    start_http_server(port)
