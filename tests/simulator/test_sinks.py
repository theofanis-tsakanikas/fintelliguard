"""Local sink writes valid JSONL; Kafka sink works against a mocked producer."""

from __future__ import annotations

import json
import sys
import types
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


def _capture_producer_conf(kafka_config) -> dict[str, Any]:
    """Build the producer against a fake confluent_kafka and return the conf it was given."""
    captured: dict[str, Any] = {}

    class _Meta:
        brokers = {0: object()}  # non-empty => warm-up sees the brokers and returns

    class _Producer:
        def __init__(self, conf):
            captured.update(conf)

        def poll(self, _timeout):  # the MSK warm-up polls to serve the token
            return 0

        def list_topics(self, timeout=None):  # warm-up checks metadata is available
            return _Meta()

    fake = types.ModuleType("confluent_kafka")
    fake.Producer = _Producer
    sys.modules["confluent_kafka"] = fake
    try:
        KafkaSink._build_producer(kafka_config)
    finally:
        sys.modules.pop("confluent_kafka", None)
    return captured


def test_default_kafka_config_is_plaintext_and_needs_no_signer():
    """The local funnel path: PLAINTEXT, so _build_producer sets no SASL and imports no signer."""
    captured = _capture_producer_conf(KafkaConfig())
    assert set(captured) == {"bootstrap.servers", "client.id"}
    assert "security.protocol" not in captured


def test_msk_config_enables_sasl_oauthbearer_with_a_token_callback():
    cfg = KafkaConfig(
        bootstrap_servers="b:9098", security_protocol="SASL_SSL", sasl_mechanism="OAUTHBEARER"
    )
    assert cfg.uses_msk_iam
    captured = _capture_producer_conf(cfg)
    assert captured["security.protocol"] == "SASL_SSL"
    assert captured["sasl.mechanisms"] == "OAUTHBEARER"
    assert callable(captured["oauth_cb"]), "MSK IAM needs an oauth_cb that signs the token"


def test_kafka_config_reads_sasl_from_env(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:9098")
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("KAFKA_SASL_MECHANISM", "OAUTHBEARER")
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    cfg = KafkaConfig.from_env()
    assert cfg.uses_msk_iam
    assert cfg.region == "eu-central-1"
