"""Health-signal collectors behind INJECTABLE clients.

Each monitor normalizes one underlying client (DLT pipeline state, Kafka consumer lag,
Model Serving p99 latency) into a signal dict. The real clients call live cloud APIs and
are deferred; tests inject fakes. `HealthMonitors.collect()` aggregates the three signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class DLTPipelineMonitor:
    """Wraps a DLT client exposing `latest_update_state(pipeline_id) -> str`."""

    def __init__(self, client: Any, pipeline_id: str) -> None:
        self._client = client
        self._pipeline_id = pipeline_id

    def collect(self) -> dict[str, Any]:
        return {
            "pipeline_id": self._pipeline_id,
            "state": self._client.latest_update_state(self._pipeline_id),
        }


class ConsumerLagMonitor:
    """Wraps a Kafka client exposing `total_lag(topic) -> int`."""

    def __init__(self, client: Any, topic: str) -> None:
        self._client = client
        self._topic = topic

    def collect(self) -> dict[str, Any]:
        return {"topic": self._topic, "lag_records": int(self._client.total_lag(self._topic))}


class ModelServingMonitor:
    """Wraps a serving-metrics client exposing `p99_latency_ms(endpoint) -> float`."""

    def __init__(self, client: Any, endpoint: str) -> None:
        self._client = client
        self._endpoint = endpoint

    def collect(self) -> dict[str, Any]:
        return {
            "endpoint": self._endpoint,
            "p99_ms": float(self._client.p99_latency_ms(self._endpoint)),
        }


@dataclass(frozen=True)
class HealthMonitors:
    """Aggregates the three monitors into one signals dict."""

    pipeline: DLTPipelineMonitor
    consumer_lag: ConsumerLagMonitor
    serving: ModelServingMonitor

    def collect(self) -> dict[str, Any]:
        return {
            "pipeline_health": self.pipeline.collect(),
            "consumer_lag": self.consumer_lag.collect(),
            "endpoint_p99": self.serving.collect(),
        }
