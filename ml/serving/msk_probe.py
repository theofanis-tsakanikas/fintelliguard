"""Connectivity probe: does the Databricks cluster actually reach MSK, privately?

Turns "terraform apply went green" into "the private path carries traffic": a cluster in the
customer-managed VPC produces a uniquely-marked record to MSK over IAM and reads it back. It
raises, loudly, if the round trip does not complete — so a broken SG rule, unregistered instance
profile, or missing PassRole fails HERE, legibly, not three layers downstream as "no data".

Two things this had to get right, both found by live diagnosis against a real cluster:

  * NOT the Spark Kafka connector. Databricks shades Kafka as `kafkashaded.*`, but AWS's
    aws-msk-iam-auth callback handler implements the UN-shaded interface, so `spark...kafka`
    dies at producer construction with NoClassDefFoundError. This uses `confluent_kafka`
    (librdkafka, native) with the AWS MSK IAM SASL signer — the same client the simulator uses.

  * POLL to serve the token before producing. With OAUTHBEARER the IAM token is delivered
    through the client's `oauth_cb`, which is only served during poll(). A produce/flush without
    first polling races ahead of the token and the connection fails with _TRANSPORT. Warming the
    producer with a poll loop (until metadata is available) is what makes the handshake succeed.

The topic is created broker-side by auto.create.topics.enable=true (MSK configuration, see
infra/aws/msk.tf) on first write — confluent_kafka's AdminClient cannot create it here (its
admin ops time out "waiting for controller" because the token is not served in time).

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


def _warm(client, timeout_s: int) -> None:
    """Poll until the OAUTHBEARER token is served and the brokers are reachable (metadata up).

    This is the fix: without the poll loop the first connection attempt races ahead of the token
    and fails with _TRANSPORT. list_topics returning brokers means the SASL/TLS handshake completed.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        client.poll(0.5)
        if client.list_topics(timeout=5).brokers:
            return
    raise RuntimeError(
        f"[probe] FAIL — could not establish an authenticated session to MSK within {timeout_s}s. "
        "Check the MSK SG ingress from the Databricks data-plane SG (9098), the instance profile, "
        "and the cross-account PassRole."
    )


def run_probe(bootstrap: str, topic: str, region: str, timeout_s: int) -> None:
    """Produce one uniquely-marked record to MSK and read it back. Raise if it does not return."""
    from confluent_kafka import Consumer, Producer, TopicPartition

    marker = f"msk-probe-{uuid.uuid4()}"

    # 1) Produce — warm the producer first so the IAM token is served, then write. The topic is
    # auto-created broker-side on this first write. The delivery report gives the exact
    # (partition, offset) the record landed at, which the read then targets.
    producer = Producer(_sasl_conf(bootstrap, region))
    _warm(producer, timeout_s)
    delivered: dict[str, int] = {}

    def _on_delivery(err, msg):
        if err is None:
            delivered["partition"] = msg.partition()
            delivered["offset"] = msg.offset()

    producer.produce(topic, value=marker.encode("utf-8"), on_delivery=_on_delivery)
    producer.flush(timeout_s)
    if "offset" not in delivered:
        raise RuntimeError(
            f"[probe] FAIL — session to MSK established but could not deliver {marker} to {topic} "
            f"within {timeout_s}s. Check topic auto-create (auto.create.topics.enable) and "
            "kafka-cluster:WriteData on the topic."
        )
    print(
        f"[probe] produced {marker} to {topic} @ p{delivered['partition']}/o{delivered['offset']}"
    )

    # 2) Consume — assign the exact (partition, offset) the write landed at (no consumer group to
    # coordinate/authorize, only kafka-cluster:ReadData). Warm it too before assigning.
    consumer = Consumer({**_sasl_conf(bootstrap, region), "group.id": f"probe-{marker}"})
    _warm(consumer, timeout_s)
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
