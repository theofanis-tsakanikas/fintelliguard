"""Runtime config is read from Spark conf, because DLT `configuration` is Spark conf.

The bronze layer read `os.environ.get("RAW_BUCKET")`; DLT never sets that, so Auto Loader
defaulted to `s3://fintelliguard-raw/...` — the bucket without its account-id suffix — and
schema inference failed on an empty path.
"""

from __future__ import annotations

import ast
from pathlib import Path

from pipelines import runtime_config


class _Conf:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _Spark:
    def __init__(self, values):
        self.conf = _Conf(values)


def test_raw_bucket_and_derived_path_come_from_spark_conf():
    spark = _Spark({"fintelliguard.raw_bucket": "fintelliguard-raw-123456789012"})
    assert runtime_config.raw_bucket(spark) == "fintelliguard-raw-123456789012"
    assert (
        runtime_config.ieee_raw_path(spark) == "s3://fintelliguard-raw-123456789012/raw/ieee-cis/"
    )


def test_an_explicit_ieee_path_overrides_the_derived_one():
    spark = _Spark({"fintelliguard.ieee_raw_path": "s3://elsewhere/x/"})
    assert runtime_config.ieee_raw_path(spark) == "s3://elsewhere/x/"


def test_a_missing_bucket_yields_no_path_rather_than_a_wrong_one():
    """The old default silently pointed Auto Loader at a bucket that does not exist."""
    assert runtime_config.ieee_raw_path(_Spark({})) == ""


def test_streaming_defaults_on_but_reads_the_flag():
    assert runtime_config.streaming_enabled(_Spark({})) is True
    assert (
        runtime_config.streaming_enabled(_Spark({"fintelliguard.streaming_enabled": "false"}))
        is False
    )


def test_none_session_is_tolerated_for_local_import():
    assert runtime_config.raw_bucket(None) == ""
    assert runtime_config.streaming_enabled(None) is True


def test_the_kafka_reader_specifies_bootstrap_servers():
    """The option was simply absent, so even a configured MSK could not be reached."""
    src = (Path(__file__).resolve().parents[2] / "pipelines/bronze/bronze_pipeline.py").read_text(
        "utf-8"
    )
    tree = ast.parse(src)
    options = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "option"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert "kafka.bootstrap.servers" in options, (
        "the Kafka readStream does not set kafka.bootstrap.servers — the source cannot connect"
    )
