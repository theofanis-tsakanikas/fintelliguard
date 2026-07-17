"""The DLT data-quality gates must be able to report a failure.

The pipelines used to do this:

    @dlt.expect_all(silver_transforms.SILVER_TXN_GATES)
    def transactions_clean() -> DataFrame:
        cleaned = silver_transforms.cleanse_transactions(dlt.read_stream(...))
        return silver_transforms.select_valid(cleaned)   # <- failures already removed

`select_valid` drops every failing row BEFORE the frame reaches DLT. The decorator then
evaluated those same gates against a frame from which all violations had been removed, so
the data-quality dashboard read 100% pass — permanently, by construction, and it would have
read 100% during a total upstream corruption event.

That is the same disease as a guardrail nobody attached and a parity test comparing
dataclass field types: a control that is green because it cannot be anything else. Here
the tell is arithmetic — you cannot fail a check you have already filtered.

These tests interrogate the frame the expectation ACTUALLY sees.
"""

from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field

import pytest

from pipelines.common import QUARANTINE_COL, select_valid

_TXN_SCHEMA = (
    "transaction_id string, timestamp string, amount double, merchant_id string, "
    "card_hash string, device_id string, ip_country string, mcc_code string"
)

_GOOD = ("t1", "2026-01-01T12:00:00+00:00", 50.0, "M1", "0" * 32, "D1", "DE", "5411")
_BAD_AMOUNT = ("t2", "2026-01-01T12:00:00+00:00", -5.0, "M2", "0" * 32, "D1", "DE", "5411")
_BAD_CARD = ("t3", "2026-01-01T12:00:00+00:00", 20.0, "M3", "not-hex", "D1", "DE", "5411")


@dataclass
class _Recorder:
    """A dlt stub that records WHICH function each expectation was attached to."""

    expectations: dict = field(default_factory=dict)
    source: object = None

    def install(self) -> None:
        stub = types.ModuleType("dlt")
        pending: dict = {}

        def expect_all(gates):
            def decorator(fn):
                pending[fn.__name__] = gates
                return fn

            return decorator

        def table(*args, **kwargs):
            def decorator(fn):
                # The expectation decorator runs first (it is innermost), so by the time
                # @dlt.table sees the function we know which gates were attached to it.
                if fn.__name__ in pending:
                    self.expectations[kwargs.get("name", fn.__name__)] = (
                        pending.pop(fn.__name__),
                        fn,
                    )
                return fn

            if args and callable(args[0]) and not kwargs:
                return args[0]
            return decorator

        def _passthrough(*args, **kwargs):
            def decorator(fn):
                return fn

            return decorator

        stub.table = table
        stub.view = table
        stub.expect_all = expect_all
        stub.expect = _passthrough
        stub.expect_or_drop = _passthrough
        stub.expect_all_or_drop = _passthrough
        stub.expect_or_fail = _passthrough
        stub.read = lambda *a, **k: self.source
        stub.read_stream = lambda *a, **k: self.source
        sys.modules["dlt"] = stub


@pytest.fixture
def silver_dq(spark):
    recorder = _Recorder()
    recorder.install()
    recorder.source = spark.createDataFrame([_GOOD, _BAD_AMOUNT, _BAD_CARD], _TXN_SCHEMA)
    module = importlib.reload(importlib.import_module("pipelines.silver.silver_pipeline"))
    return recorder, module


def test_the_expectation_is_attached_to_a_frame_that_still_contains_failures(silver_dq):
    """The whole fix, in one assertion.

    If the gated frame contains no failing rows, the DQ percentage is 100% and there is no
    input for which it is anything else.
    """
    recorder, _module = silver_dq
    assert "silver.transactions_gated" in recorder.expectations, (
        "the transaction gates are not measured on a gated view — if they are back on the "
        "clean table, they are measured on rows that have already been filtered"
    )

    _gates, fn = recorder.expectations["silver.transactions_gated"]
    measured = fn().collect()

    reasons = {r["transaction_id"]: r[QUARANTINE_COL] for r in measured}
    assert reasons == {
        "t1": None,
        "t2": "amount_positive_bounded",
        "t3": "valid_card_hash",
    }, "the frame the expectation sees has had its failures removed — it can only read 100%"


def test_the_dq_metric_can_be_less_than_one_hundred_percent(silver_dq):
    """The property that was arithmetically impossible before."""
    recorder, _module = silver_dq
    _gates, fn = recorder.expectations["silver.transactions_gated"]

    measured = fn()
    total = measured.count()
    passing = select_valid(measured).count()

    assert total > 0
    assert passing < total, (
        f"{passing}/{total} rows pass — with failures pre-filtered this ratio is 1.0 for "
        "every possible input, including a totally corrupt upstream"
    )


def test_the_clean_table_still_contains_only_clean_rows(silver_dq):
    """Measuring on the unfiltered view must not weaken what the clean table guarantees.

    `expect_all` (not `_or_drop`) is deliberate: the split is what enforces, the
    expectation is what measures. Both, in the right places.
    """
    recorder, module = silver_dq
    _gates, gated_fn = recorder.expectations["silver.transactions_gated"]
    recorder.source = gated_fn()  # the clean table reads from the gated view

    clean = module.transactions_clean().collect()
    assert [r["transaction_id"] for r in clean] == ["t1"]

    quarantined = module.transactions_quarantine().collect()
    assert {r["transaction_id"] for r in quarantined} == {"t2", "t3"}


def test_gold_gates_are_measured_on_an_unfiltered_frame_too(spark):
    """The same bug existed on both gold tables."""
    recorder = _Recorder()
    recorder.install()
    importlib.reload(importlib.import_module("pipelines.gold.gold_pipeline"))

    assert "gold.txn_features_realtime_gated" in recorder.expectations
    assert "gold.txn_features_training_gated" in recorder.expectations
