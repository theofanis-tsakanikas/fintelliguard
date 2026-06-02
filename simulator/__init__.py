"""FintelliGuard synthetic transaction simulator.

Generates the bronze stream contract (`docs/data-flow.md`) with realistic, learnable
fraud patterns, behind pluggable local / Kafka sinks.
"""

from __future__ import annotations

from .config import KafkaConfig, SimulatorConfig, SinkType
from .generator import FraudPattern, TransactionGenerator
from .runner import SimulatorRunner
from .schema import CONTRACT_FIELDS, LABEL_FIELDS, Transaction
from .sinks import KafkaSink, LocalSink, Sink, build_sink

__all__ = [
    "CONTRACT_FIELDS",
    "LABEL_FIELDS",
    "FraudPattern",
    "KafkaConfig",
    "KafkaSink",
    "LocalSink",
    "SimulatorConfig",
    "SimulatorRunner",
    "Sink",
    "SinkType",
    "Transaction",
    "TransactionGenerator",
    "build_sink",
]
