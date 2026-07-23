"""The MSK probe uses the confluent_kafka client path (not the Spark connector) with IAM.

The Spark Kafka connector dies on Databricks' shaded Kafka classes when the AWS IAM callback
handler loads; the probe therefore uses confluent_kafka (native librdkafka) + the AWS MSK IAM
signer, the SAME client path the simulator's producer uses. These pin that it builds the
SASL_SSL/OAUTHBEARER config with a token callback, and that it demands a bootstrap target.
"""

from __future__ import annotations

import pytest

from ml.serving import msk_probe


def test_sasl_conf_is_sasl_ssl_oauthbearer_with_a_token_callback():
    conf = msk_probe._sasl_conf("b-1:9098,b-2:9098", "eu-central-1")
    assert conf["bootstrap.servers"] == "b-1:9098,b-2:9098"
    assert conf["security.protocol"] == "SASL_SSL"
    assert conf["sasl.mechanisms"] == "OAUTHBEARER"
    # The callback signs a fresh AWS MSK IAM token at runtime — no stored secret.
    assert callable(conf["oauth_cb"])


def test_sasl_conf_matches_the_simulator_producer_path():
    """Probe and simulator must authenticate to MSK the same way, or the probe proves nothing."""
    from simulator.config import KafkaConfig
    from simulator.sinks import KafkaSink

    captured: dict = {}

    class _P:
        def __init__(self, c):
            captured.update(c)

    import sys
    import types as _t

    fake = _t.ModuleType("confluent_kafka")
    fake.Producer = _P
    sys.modules["confluent_kafka"] = fake
    try:
        KafkaSink._build_producer(
            KafkaConfig(
                bootstrap_servers="b:9098",
                security_protocol="SASL_SSL",
                sasl_mechanism="OAUTHBEARER",
            )
        )
    finally:
        sys.modules.pop("confluent_kafka", None)

    probe = msk_probe._sasl_conf("b:9098", "eu-central-1")
    assert probe["security.protocol"] == captured["security.protocol"] == "SASL_SSL"
    assert probe["sasl.mechanisms"] == captured["sasl.mechanisms"] == "OAUTHBEARER"


def test_probe_requires_a_bootstrap_target():
    """No --bootstrap means no way to reach MSK — argparse must reject it, not default silently."""
    with pytest.raises(SystemExit):
        msk_probe.main([])
