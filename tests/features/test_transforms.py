"""Shared transforms must be correct on known inputs."""

from __future__ import annotations

import math

from ml.features import transforms
from ml.features.transforms import circular_hour_distance, is_unusual_hour


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


# --------------------------------------------------------------------------- #
# is_unusual_hour: hours are circular, and the old rule pretended otherwise
# --------------------------------------------------------------------------- #


def test_circular_distance_goes_the_short_way_round_the_clock():
    assert circular_hour_distance(23, 1) == 2
    assert circular_hour_distance(1, 23) == 2
    assert circular_hour_distance(0, 12) == 12
    assert circular_hour_distance(9, 9) == 0


def test_midnight_spanning_card_still_has_unusual_hours():
    """The bug: `min(seen) <= hour <= max(seen)` on a circular quantity.

    A card seen at 22:00, 23:00, 00:00 and 01:00 gets the linear band [0, 23] — which
    contains every hour of the day, so NOTHING is ever unusual for it again. Any card that
    transacts either side of midnight had a permanently dead feature.
    """
    seen = [22, 23, 0, 1]
    assert is_unusual_hour(12, seen) is True, "midday is far from a card that lives at night"
    assert is_unusual_hour(23, seen) is False
    assert is_unusual_hour(0, seen) is False


def test_a_card_with_thin_history_is_not_judged_by_it():
    """Below `MIN_HOURS_FOR_BAND`, use the population prior rather than invent a pattern.

    Two observations do not make a routine. Scoring every other hour of a card's ordinary
    working day as "unusual" flagged ~17% of legitimate traffic and buried the signal.
    """
    thin = [10, 11]
    assert is_unusual_hour(18, thin) is False  # daytime, no established pattern -> prior
    assert is_unusual_hour(3, thin) is True  # night -> prior says unusual


def test_an_established_daytime_card_flags_the_small_hours():
    seen = [9, 10, 11, 12, 13, 14]
    assert is_unusual_hour(3, seen) is True
    assert is_unusual_hour(12, seen) is False
    assert is_unusual_hour(15, seen) is False  # 1h past the band, within tolerance
