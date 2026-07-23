"""Runtime configuration for the DLT pipeline, read from Spark conf.

Why this exists
---------------
A DLT `configuration:` entry arrives on the pipeline cluster as SPARK CONF, not as an
environment variable. The bronze layer read `os.environ.get("RAW_BUCKET")`, which the
pipeline never sets, so Auto Loader fell back to `s3://fintelliguard-raw/...` — the bucket
name without its `-<account_id>` suffix — and schema inference failed on an empty path
(deploy run 29789516013). The comment there even claimed "the DABs job injects RAW_BUCKET";
nothing did. These read the values where DLT actually puts them.

Two modes in one pipeline
-------------------------
The pipeline declares both lineages: the Kafka stream (`transactions_stream` -> realtime
features) and the IEEE-CIS batch (`ieee_cis_raw` -> training features). The stream needs MSK,
a topic, and the simulator producing to it; a Kafka source with no `bootstrap.servers` cannot
even be ANALYZED, so its mere presence failed the whole pipeline —

    IllegalArgumentException: Option 'kafka.bootstrap.servers' must be specified

`streaming_enabled` gates the stream lineage. It DEFAULTS ON: the pipeline is built for both
modes and the local tests exercise the full graph. The training-only deploy sets it false,
because that run brings up neither MSK nor the simulator, and the batch path is self-contained
(S3 CSVs -> gold training table).
"""

from __future__ import annotations

from typing import Any

_PREFIX = "fintelliguard."


def _conf(spark: Any, key: str, default: str = "") -> str:
    """Read one Spark-conf value, tolerant of the local (no-session) import."""
    if spark is None:
        return default
    try:
        return spark.conf.get(_PREFIX + key, default)
    except Exception:  # noqa: BLE001 - conf key absent on some runtimes raises rather than defaulting
        return default


def raw_bucket(spark: Any) -> str:
    return _conf(spark, "raw_bucket")


def ieee_raw_path(spark: Any) -> str:
    """The Auto Loader source. An explicit override wins; else derive it from the bucket."""
    explicit = _conf(spark, "ieee_raw_path")
    if explicit:
        return explicit
    bucket = raw_bucket(spark)
    return f"s3://{bucket}/raw/ieee-cis/" if bucket else ""


def kafka_bootstrap(spark: Any) -> str:
    return _conf(spark, "kafka_bootstrap")


def kafka_security_protocol(spark: Any) -> str:
    """PLAINTEXT for local Docker Kafka (the default), SASL_SSL for MSK with IAM auth."""
    return _conf(spark, "kafka_security_protocol", "PLAINTEXT").strip().upper()


def kafka_source_options(spark: Any) -> dict[str, str]:
    """Extra reader options for the Kafka source.

    Empty for local PLAINTEXT Kafka, so the local funnel is untouched. When the security
    protocol is SASL_SSL (MSK), returns the AWS_MSK_IAM SASL wiring: the reader authenticates
    to the brokers using the cluster's instance-profile credentials (see infra/aws
    streaming.tf + infra/databricks streaming.tf), with no secret in code or conf.
    """
    if kafka_security_protocol(spark) != "SASL_SSL":
        return {}
    return {
        "kafka.security.protocol": "SASL_SSL",
        "kafka.sasl.mechanism": "AWS_MSK_IAM",
        "kafka.sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
        "kafka.sasl.client.callback.handler.class": (
            "software.amazon.msk.auth.iam.IAMClientCallbackHandler"
        ),
    }


def streaming_enabled(spark: Any) -> bool:
    """Whether the Kafka stream lineage is part of this run. Default TRUE (see module docs)."""
    return _conf(spark, "streaming_enabled", "true").strip().lower() == "true"
