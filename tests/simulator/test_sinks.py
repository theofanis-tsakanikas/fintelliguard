"""Local sink writes valid JSONL; Kafka sink works against a mocked producer."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

from simulator import (
    KafkaConfig,
    KafkaSink,
    LocalSink,
    SimulatorConfig,
    SimulatorRunner,
    Sink,
    build_sink,
)
from simulator.config import SinkType


def test_local_sink_writes_valid_jsonl(tmp_path):
    path = tmp_path / "out.jsonl"
    records = [{"transaction_id": "a", "amount": 1.5}, {"transaction_id": "b", "amount": 2.0}]
    with LocalSink(str(path)) as sink:
        for record in records:
            sink.emit(record)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(records)
    assert [json.loads(line) for line in lines] == records


def test_kafka_sink_uses_injected_producer_without_real_broker():
    producer = Mock()
    sink = KafkaSink(KafkaConfig(topic="txn.raw"), producer=producer)
    sink.emit({"card_hash": "abc123", "amount": 9.9})

    producer.produce.assert_called_once()
    _, kwargs = producer.produce.call_args
    assert producer.produce.call_args.args[0] == "txn.raw"
    assert json.loads(kwargs["value"].decode("utf-8")) == {"card_hash": "abc123", "amount": 9.9}
    assert kwargs["key"] == b"abc123"

    sink.close()
    producer.flush.assert_called_once()


def test_build_sink_selects_local_by_default():
    assert isinstance(build_sink(SimulatorConfig()), LocalSink)


class _CollectingSink(Sink):
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def emit(self, record: dict[str, Any]) -> None:
        self.records.append(record)


def test_runner_respects_max_records_and_emits_labels():
    sink = _CollectingSink()
    config = SimulatorConfig(seed=2, max_records=50, fraud_injection_rate=0.2)
    runner = SimulatorRunner(config, sink=sink, sleep=lambda _seconds: None)

    emitted = runner.run()
    assert emitted == 50
    assert len(sink.records) == 50
    assert all("is_fraud_truth" in record for record in sink.records)


def test_runner_can_omit_ground_truth_for_production_path():
    sink = _CollectingSink()
    config = SimulatorConfig(
        seed=2, max_records=20, fraud_injection_rate=0.2, emit_ground_truth=False
    )
    runner = SimulatorRunner(config, sink=sink, sleep=lambda _seconds: None)

    runner.run()
    assert sink.records
    for record in sink.records:
        assert "is_fraud_truth" not in record
        assert "fraud_pattern" not in record


def test_sink_type_enum_round_trips():
    assert SinkType("local") is SinkType.LOCAL
    assert SinkType("kafka") is SinkType.KAFKA
