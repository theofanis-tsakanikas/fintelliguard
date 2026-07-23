"""Output sinks behind one small interface.

`LocalSink` needs zero infrastructure (stdout or a JSONL file) so the simulator runs and
is tested today. `KafkaSink` is config-gated for later; its producer is injectable so it
unit-tests against a mock with no real broker.
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from typing import Any, TextIO

from .config import KafkaConfig, SimulatorConfig, SinkType


class Sink(ABC):
    """A destination for serialized transaction records."""

    @abstractmethod
    def emit(self, record: dict[str, Any]) -> None:
        """Write a single record."""

    def close(self) -> None:
        """Flush/close any resources. No-op by default."""
        return

    def __enter__(self) -> Sink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class LocalSink(Sink):
    """Write newline-delimited JSON to a file (when `path` is set) or stdout."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path
        self._owns_handle = path is not None
        self._handle: TextIO = open(path, "w", encoding="utf-8") if path else sys.stdout

    def emit(self, record: dict[str, Any]) -> None:
        self._handle.write(json.dumps(record) + "\n")

    def close(self) -> None:
        self._handle.flush()
        if self._owns_handle:
            self._handle.close()


class KafkaSink(Sink):
    """Publish records to Kafka. The producer is injected for testability."""

    def __init__(self, kafka_config: KafkaConfig, producer: Any | None = None) -> None:
        self._topic = kafka_config.topic
        self._producer = producer if producer is not None else self._build_producer(kafka_config)

    @staticmethod
    def _build_producer(kafka_config: KafkaConfig) -> Any:
        # Imported lazily so the package (and its tests) need no Kafka client installed.
        from confluent_kafka import Producer

        conf: dict[str, Any] = {
            "bootstrap.servers": kafka_config.bootstrap_servers,
            "client.id": kafka_config.client_id,
        }
        # MSK IAM: SASL_SSL/OAUTHBEARER with a token signed at runtime from the task's IAM
        # role (no stored secret). Absent for local PLAINTEXT, so the local funnel is untouched.
        if kafka_config.uses_msk_iam:
            conf["security.protocol"] = "SASL_SSL"
            conf["sasl.mechanisms"] = "OAUTHBEARER"
            conf["oauth_cb"] = KafkaSink._msk_iam_oauth_cb(kafka_config.region)

        return Producer(conf)

    @staticmethod
    def _msk_iam_oauth_cb(region: str):
        """confluent-kafka OAUTHBEARER callback returning a fresh AWS MSK IAM token."""

        def _cb(_config: str):
            # Lazy import: only the MSK path needs the signer, so local/tests never require it.
            from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

            token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(region)
            # librdkafka wants the absolute expiry in SECONDS since epoch.
            return token, expiry_ms / 1000.0

        return _cb

    def emit(self, record: dict[str, Any]) -> None:
        # Key by card_hash so a card's events keep order within a partition.
        key = record.get("card_hash")
        self._producer.produce(
            self._topic,
            value=json.dumps(record).encode("utf-8"),
            key=key.encode("utf-8") if isinstance(key, str) else None,
        )

    def close(self) -> None:
        self._producer.flush()


def build_sink(config: SimulatorConfig) -> Sink:
    """Construct the sink selected by `config`."""
    if config.sink is SinkType.KAFKA:
        return KafkaSink(KafkaConfig.from_env())
    return LocalSink(config.jsonl_path)
