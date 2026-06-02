"""Shared transforms must be correct on known inputs."""

from __future__ import annotations

import math

from ml.features import transforms


def test_amount_log_is_log1p():
    assert transforms.amount_log(0.0) == 0.0
    assert transforms.amount_log(99.0) == math.log1p(99.0)


def test_zscore_and_zero_std_guard():
    assert transforms.zscore(10.0, 5.0, 2.5) == 2.0
    assert transforms.zscore(10.0, 5.0, 0.0) == 0.0
    assert transforms.zscore(1.0, 5.0, -1.0) == 0.0


def test_values_differ_handles_unknown_reference():
    assert transforms.values_differ("US", "US") is False
    assert transforms.values_differ("US", "DE") is True
    assert transforms.values_differ("US", None) is False


def test_is_unusual_hour_uses_history_then_night_fallback():
    # No history -> night heuristic.
    assert transforms.is_unusual_hour(3, None) is True
    assert transforms.is_unusual_hour(12, None) is False
    # With history -> deviation from seen hours.
    assert transforms.is_unusual_hour(3, {8, 9, 10}) is True
    assert transforms.is_unusual_hour(9, {8, 9, 10}) is False


def test_clamps_and_lookups():
    assert transforms.clamp_score(1.5) == 1.0
    assert transforms.clamp_score(-0.1) == 0.0
    assert transforms.clamp_tier(0) == 1
    assert transforms.clamp_tier(9) == 5
    assert transforms.risk_score("a", {"a": 0.4}) == 0.4
    assert transforms.risk_score("missing", {}, default=0.0) == 0.0
    assert transforms.risk_tier("a", {"a": 5}) == 5
    assert transforms.risk_tier("missing", {}, default=2) == 2
