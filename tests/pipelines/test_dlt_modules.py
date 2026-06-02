"""The @dlt.* pipeline modules import cleanly (decorators wired) under a dlt stub.

Full DLT + Structured Streaming execution is deferred to the deploy phase; here we only
validate that the thin decorator layer imports and registers its table functions.
"""

from __future__ import annotations

import importlib
import sys
import types


def _install_dlt_stub() -> None:
    stub = types.ModuleType("dlt")

    def table(*args, **kwargs):
        if args and callable(args[0]) and not kwargs:
            return args[0]

        def decorator(fn):
            return fn

        return decorator

    def passthrough(*args, **kwargs):
        def decorator(fn):
            return fn

        return decorator

    stub.table = table
    stub.view = table
    stub.expect = passthrough
    stub.expect_all = passthrough
    stub.expect_or_drop = passthrough
    stub.expect_all_or_drop = passthrough
    stub.expect_or_fail = passthrough
    stub.read = lambda *a, **k: None
    stub.read_stream = lambda *a, **k: None
    sys.modules["dlt"] = stub


def test_pipeline_modules_import_and_register_tables():
    _install_dlt_stub()

    bronze = importlib.import_module("pipelines.bronze.bronze_pipeline")
    silver = importlib.import_module("pipelines.silver.silver_pipeline")
    gold = importlib.import_module("pipelines.gold.gold_pipeline")

    assert callable(bronze.transactions_stream)
    assert callable(bronze.ieee_cis_raw)
    assert callable(silver.transactions_clean)
    assert callable(silver.transactions_quarantine)
    assert callable(silver.ieee_cis_clean)
    assert callable(silver.ieee_cis_quarantine)
    assert callable(gold.txn_features_realtime)
    assert callable(gold.txn_features_realtime_quarantine)
    assert callable(gold.txn_features_training)
