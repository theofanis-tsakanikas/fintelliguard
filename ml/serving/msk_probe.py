"""Connectivity probe: does the Databricks classic cluster actually reach MSK, privately?

This is the gate for the network stage — the thing that makes "terraform apply went green"
into "the private path carries traffic". A green apply proves the resources exist; it does NOT
prove a Spark cluster in the customer-managed VPC can open 9098 to a broker and authenticate
with IAM. The security-group rule could be wrong, the instance profile could be unregistered,
the PassRole could be missing — all of which apply cleanly and then fail at the first read,
three layers downstream, as "no data". This probe moves that failure to where the reason is
legible: it does a full Kafka round-trip and exits non-zero, loudly, if the path is down.

Runs as a Databricks Spark job task on a single-node cluster that carries the MSK instance
profile (see infra/bundles/resources/streaming_probe.yml). Auth is the instance profile — no
secret in code or args.

    spark-submit msk_probe.py --bootstrap <brokers> [--topic txn.probe] [--timeout 120]
"""

from __future__ import annotations

import argparse
import time
import uuid

# MSK IAM SASL reader/writer options. INLINED, not imported from pipelines.runtime_config: this
# script runs as a bare Databricks spark_python_task whose sys.path does not include the synced
# `pipelines` package (only a wheel would), so importing it fails with ModuleNotFoundError on
# the cluster. A local parity test (tests/serving/test_msk_probe) asserts these stay identical
# to runtime_config.kafka_source_options(SASL_SSL), so the probe still proves the SAME options
# the DLT consumer uses — the guarantee just moves from runtime import to test time.
_MSK_IAM_OPTIONS = {
    "kafka.security.protocol": "SASL_SSL",
    "kafka.sasl.mechanism": "AWS_MSK_IAM",
    "kafka.sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
    "kafka.sasl.client.callback.handler.class": (
        "software.amazon.msk.auth.iam.IAMClientCallbackHandler"
    ),
}


def _spark():
    from pyspark.sql import SparkSession

    return SparkSession.builder.getOrCreate()


def _iam_options() -> dict[str, str]:
    """The SASL_SSL + AWS_MSK_IAM options the probe forces (see _MSK_IAM_OPTIONS)."""
    return dict(_MSK_IAM_OPTIONS)


def run_probe(bootstrap: str, topic: str, timeout_s: int) -> None:
    """Write one uniquely-marked record to MSK and read it back. Raise if it does not return."""
    spark = _spark()
    marker = f"msk-probe-{uuid.uuid4()}"
    opts = _iam_options()

    # 1) Produce — proves WriteData + Connect over the private path.
    row = spark.createDataFrame([(marker,)], ["value"])
    writer = row.selectExpr("CAST(value AS STRING) AS value").write.format("kafka")
    writer = writer.option("kafka.bootstrap.servers", bootstrap).option("topic", topic)
    for key, value in opts.items():
        writer = writer.option(key, value)
    writer.save()
    print(f"[probe] produced {marker} to {topic}")

    # 2) Consume — proves ReadData + the round trip. Batch read from the earliest offset,
    # retried until the marker shows up or the timeout expires (brokers settle asynchronously).
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        reader = spark.read.format("kafka")
        reader = (
            reader.option("kafka.bootstrap.servers", bootstrap)
            .option("subscribe", topic)
            .option("startingOffsets", "earliest")
        )
        for key, value in opts.items():
            reader = reader.option(key, value)
        found = (
            reader.load()
            .selectExpr("CAST(value AS STRING) AS value")
            .where(f"value = '{marker}'")
            .limit(1)
            .count()
        )
        if found:
            print(f"[probe] PASS — round-tripped {marker} over the private MSK path")
            return
        time.sleep(5)

    # RuntimeError, not SystemExit/sys.exit: inside the Databricks job host SystemExit is
    # reported as INTERNAL_ERROR (even for 0), swallowing the reason. A normal exception fails
    # the task cleanly with this message. See tests/bundles test_a_task_script_never_calls_sys_exit.
    raise RuntimeError(
        f"[probe] FAIL — produced {marker} but could not read it back within {timeout_s}s. "
        "The private path to MSK is not working: check the MSK SG ingress from the Databricks "
        "data-plane SG (9098), the registered instance profile, and the cross-account PassRole."
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Probe the private Databricks→MSK path.")
    ap.add_argument("--bootstrap", required=True, help="MSK IAM SASL bootstrap brokers")
    ap.add_argument("--topic", default="txn.probe")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args(argv)
    run_probe(args.bootstrap, args.topic, args.timeout)


# No sys.exit wrapper: on success the task must finish normally, and on failure run_probe
# raises — SystemExit here would report even a clean run as INTERNAL_ERROR in the job host.
if __name__ == "__main__":
    main()
