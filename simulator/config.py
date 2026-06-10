"""Typed configuration for the simulator.

Nothing secret lives here. Kafka connection details come from the environment
(`KafkaConfig.from_env`), never hardcoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class SinkType(StrEnum):
    """Where generated transactions are written."""

    LOCAL = "local"
    KAFKA = "kafka"


@dataclass(frozen=True)
class KafkaConfig:
    """Kafka connection settings, sourced from the environment."""

    bootstrap_servers: str = "localhost:9092"
    topic: str = "txn.raw"
    client_id: str = "fintelliguard-simulator"

    @classmethod
    def from_env(cls) -> KafkaConfig:
        """Build from `KAFKA_BOOTSTRAP_SERVERS` / `KAFKA_TOPIC` / `KAFKA_CLIENT_ID`."""
        return cls(
            bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap_servers),
            topic=os.environ.get("KAFKA_TOPIC", cls.topic),
            client_id=os.environ.get("KAFKA_CLIENT_ID", cls.client_id),
        )


@dataclass(frozen=True)
class SimulatorConfig:
    """Tunable simulator behaviour.

    `seed` makes a run fully deterministic. `duration_seconds`/`max_records` bound the
    run; both `None` means unbounded.
    """

    rate_per_sec: float = 500.0
    duration_seconds: float | None = None
    max_records: int | None = None
    fraud_injection_rate: float = 0.01
    seed: int = 42

    # Ground truth is emitted for demo/eval by default. Disable for the production Kafka
    # path so the model never sees labels.
    emit_ground_truth: bool = True

    # Sink selection.
    sink: SinkType = SinkType.LOCAL
    jsonl_path: str | None = None  # None + LOCAL sink => stdout

    # Population sizes for the synthetic entity pools.
    n_cards: int = 1000
    n_merchants: int = 200
    n_devices: int = 1500

    def __post_init__(self) -> None:
        if self.rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if not 0.0 <= self.fraud_injection_rate <= 1.0:
            raise ValueError("fraud_injection_rate must be in [0, 1]")
        if self.n_cards < 1 or self.n_merchants < 1 or self.n_devices < 1:
            raise ValueError("entity pool sizes must be >= 1")
