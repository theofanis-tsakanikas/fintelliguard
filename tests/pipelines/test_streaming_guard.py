"""The Kafka stream lineage must not be part of a batch-only (training) run.

The medallion pipeline declares two lineages in one file set: the Kafka stream
(`transactions_stream` -> realtime features) and the IEEE-CIS batch (`ieee_cis_raw` ->
training features). A Kafka source with no `bootstrap.servers` cannot even be ANALYZED —

    IllegalArgumentException: Option 'kafka.bootstrap.servers' must be specified

so its mere presence failed the entire pipeline on the first real run (deploy 29789516013),
including the batch tables the training job needs. The training deploy sets
`fintelliguard.streaming_enabled=false`; the stream lineage must then not register, while the
batch lineage always does.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

_STREAM = {
    "bronze.transactions_stream",
    "transactions_gated",
    "silver.transactions_clean",
    "silver.transactions_quarantine",
    "txn_features_realtime_gated",
    "gold.txn_features_realtime",
    "gold.txn_features_realtime_quarantine",
}
_BATCH = {
    "bronze.ieee_cis_raw",
    "ieee_cis_gated",
    "silver.ieee_cis_clean",
    "silver.ieee_cis_quarantine",
    "txn_features_training_gated",
    "gold.txn_features_training",
}


class _FakeConf:
    def __init__(self, values: dict):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _FakeSpark:
    def __init__(self, conf: dict):
        self.conf = _FakeConf(conf)
        self.readStream = self  # never actually called during registration

    def __getattr__(self, _name):  # tolerate .format(...).option(...) chains if reached
        return lambda *a, **k: self


def _registered_names(conf: dict) -> set[str]:
    """Reload all three pipeline modules under a given Spark conf; return registered names."""
    names: set[str] = set()

    stub = types.ModuleType("dlt")

    def _decorator(*_a, name=None, **_k):
        def wrap(fn):
            names.add(name or fn.__name__)
            return fn

        # @dlt.table used bare (no args) vs with kwargs — here always called with name=.
        return wrap

    stub.table = _decorator
    stub.view = _decorator
    stub.expect_all = lambda *_a, **_k: lambda fn: fn
    stub.expect = stub.expect_or_drop = stub.expect_all_or_drop = stub.expect_or_fail = (
        lambda *_a, **_k: lambda fn: fn
    )
    stub.read = stub.read_stream = lambda *_a, **_k: None
    sys.modules["dlt"] = stub

    from pyspark.sql import SparkSession

    original = SparkSession.getActiveSession
    SparkSession.getActiveSession = staticmethod(lambda: _FakeSpark(conf))
    try:
        for module in (
            "pipelines.bronze.bronze_pipeline",
            "pipelines.silver.silver_pipeline",
            "pipelines.gold.gold_pipeline",
        ):
            importlib.reload(importlib.import_module(module))
    finally:
        SparkSession.getActiveSession = original
        sys.modules.pop("dlt", None)
    return names


@pytest.fixture(autouse=True)
def _restore_modules():
    yield
    # Leave the real modules importable for other tests.
    for module in (
        "pipelines.bronze.bronze_pipeline",
        "pipelines.silver.silver_pipeline",
        "pipelines.gold.gold_pipeline",
    ):
        sys.modules.pop(module, None)


def test_streaming_off_registers_only_the_batch_lineage():
    names = _registered_names({"fintelliguard.streaming_enabled": "false"})
    assert _BATCH <= names, f"batch tables missing under streaming-off: {_BATCH - names}"
    leaked = _STREAM & names
    assert not leaked, (
        f"stream tables registered in a batch-only run: {leaked} — a Kafka source with no "
        "bootstrap servers cannot be analyzed and fails the whole pipeline"
    )


def test_streaming_on_registers_both_lineages():
    names = _registered_names({"fintelliguard.streaming_enabled": "true"})
    assert _BATCH <= names and _STREAM <= names, (
        f"missing under streaming-on: batch={_BATCH - names} stream={_STREAM - names}"
    )


def test_streaming_defaults_on_when_unconfigured():
    """Local tests and the full-graph design rely on the default being ON."""
    names = _registered_names({})
    assert _STREAM <= names, "streaming lineage absent when the flag is unset — default is ON"
