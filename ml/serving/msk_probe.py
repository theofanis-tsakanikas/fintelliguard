"""Connectivity probe: does the Databricks cluster actually reach MSK, privately?

This is the gate that turns "terraform apply went green" into "the private path carries
traffic". A green apply proves the resources exist; it does NOT prove a cluster in the
customer-managed VPC can open 9098 to a broker and authenticate with IAM. The SG rule could
be wrong, the instance profile unregistered, the PassRole missing — all apply cleanly and then
fail at the first read. This probe does a full Kafka round-trip and raises, loudly, if it can't.

Why NOT the Spark Kafka connector: on Databricks the Kafka classes are shaded as
`kafkashaded.org.apache.kafka.*`, but the AWS `aws-msk-iam-auth` library's callback handler
implements the UN-shaded `org.apache.kafka.common.security.auth.AuthenticateCallbackHandler`, so
`spark.write.format("kafka")` with IAM dies at producer construction with NoClassDefFoundError —
BEFORE any network I/O. This probe uses `confluent_kafka` (librdkafka, native — no JVM shading)
with the AWS MSK IAM SASL signer, the SAME client path the simulator/consumer use, so it proves
the real thing rather than crashing on a packaging mismatch.

Runs as a Databricks job task on a single-node cluster that carries the MSK instance profile
(see infra/bundles/streaming/streaming_probe.yml). Auth is the instance profile: the SASL token
is signed at runtime from those credentials — no secret in code or args.

    python msk_probe.py --bootstrap <brokers> [--topic txn.probe] [--region <r>] [--timeout 150]
"""

from __future__ import annotations

import argparse
import time
import uuid


def _oauth_cb(region: str):
    """confluent_kafka OAUTHBEARER callback: a fresh AWS MSK IAM token from the task's role."""

    def _cb(_config: str):
        from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

        token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(region)
        # librdkafka wants the absolute expiry in SECONDS since epoch.
        return token, expiry_ms / 1000.0

    return _cb


def _sasl_conf(bootstrap: str, region: str) -> dict:
    """Base SASL_SSL / AWS_MSK_IAM (OAUTHBEARER) client config shared by producer and consumer."""
    return {
        "bootstrap.servers": bootstrap,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "OAUTHBEARER",
        "oauth_cb": _oauth_cb(region),
    }


def run_probe(bootstrap: str, topic: str, region: str, timeout_s: int) -> None:
    """Produce one uniquely-marked record to MSK and read it back. Raise if it does not return."""
    from confluent_kafka import Consumer, Producer, TopicPartition

    marker = f"msk-probe-{uuid.uuid4()}"

    # 1) Produce — proves Connect + WriteData over the private path with IAM auth. The delivery
    # report gives the EXACT partition+offset the record landed at, which the read then targets.
    delivered: dict[str, int] = {}

    def _on_delivery(err, msg):
        if err is None:
            delivered["partition"] = msg.partition()
            delivered["offset"] = msg.offset()

    producer = Producer(_sasl_conf(bootstrap, region))
    producer.poll(0)  # prime the OAUTHBEARER token before the first metadata request
    producer.produce(topic, value=marker.encode("utf-8"), on_delivery=_on_delivery)
    remaining = producer.flush(timeout_s)
    if remaining or "offset" not in delivered:
        raise RuntimeError(
            f"[probe] FAIL — could not deliver {marker} to {topic} within {timeout_s}s. "
            "The private path to MSK is not working: check the MSK SG ingress from the Databricks "
            "data-plane SG (9098), the instance profile, and the cross-account PassRole."
        )
    print(
        f"[probe] produced {marker} to {topic} @ p{delivered['partition']}/o{delivered['offset']}"
    )

    # 2) Consume — proves ReadData + the round trip. ASSIGN the exact (partition, offset) the
    # write landed at, rather than subscribe(): no consumer group to coordinate or authorize
    # (only kafka-cluster:ReadData is needed), and no rebalance/offset-reset timing to wait on.
    consumer = Consumer({**_sasl_conf(bootstrap, region), "group.id": f"probe-{marker}"})
    consumer.assign([TopicPartition(topic, delivered["partition"], delivered["offset"])])
    try:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            msg = consumer.poll(5.0)
            if msg is None or msg.error():
                continue
            value = msg.value()
            if value is not None and value.decode("utf-8") == marker:
                print(f"[probe] PASS — round-tripped {marker} over the private MSK path")
                return
    finally:
        consumer.close()

    raise RuntimeError(
        f"[probe] FAIL — produced {marker} but could not read it back within {timeout_s}s. "
        "The brokers were reachable for the write; the read did not return the record in time."
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Probe the private Databricks->MSK path.")
    ap.add_argument("--bootstrap", required=True, help="MSK IAM SASL bootstrap brokers")
    ap.add_argument("--topic", default="txn.probe")
    ap.add_argument("--region", default="eu-central-1")
    ap.add_argument("--timeout", type=int, default=150)
    args = ap.parse_args(argv)
    run_probe(args.bootstrap, args.topic, args.region, args.timeout)


# No sys.exit wrapper: on success the task finishes normally, and on failure run_probe raises —
# SystemExit here would report even a clean run as INTERNAL_ERROR in the Databricks job host.
if __name__ == "__main__":
    main()
