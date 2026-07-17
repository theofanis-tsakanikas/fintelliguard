"""Shared, source-independent feature transforms.

Both adapters call these so source-independent features cannot diverge between training
(IEEE) and serving (stream). Pure functions only — no Spark, no IO.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping

# Hours considered "night" — the default unusual-hour heuristic when a card has no
# established active-hour history yet.
NIGHT_HOURS: frozenset[int] = frozenset(range(0, 6))


def amount_log(amount: float) -> float:
    """log1p of the amount, for skew handling (feature #2)."""
    return math.log1p(amount)


def zscore(value: float, mean: float, std: float) -> float:
    """Z-score; 0.0 when std is non-positive (too little history to be meaningful)."""
    if std <= 0.0:
        return 0.0
    return (value - mean) / std


def modal_value(values: Iterable[object]) -> object | None:
    """The most common value; ties broken by first appearance. None when there are none.

    Shared because `country_mismatch` compares against it on BOTH sides of the train/serve
    boundary: the stream adapter derives it from the card's history, and the batch job
    derives it as `CardContext.modal_addr2`. It lived privately in `adapter_stream` and was
    re-implemented for the training context, so a card with two equally-common countries
    resolved its modal country differently in training than in serving, and
    `country_mismatch` silently inverted for exactly those cards.
    """
    counts = Counter(values)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def values_differ(value: object, reference: object | None) -> bool:
    """True if `value` differs from a known `reference`; False when reference is unknown."""
    if reference is None:
        return False
    return value != reference


# How far (in hours, the short way round the clock) a transaction may sit from the card's
# nearest established active hour before it counts as unusual.
HOUR_TOLERANCE = 2

# Distinct hours a card must have been seen at before its own pattern is trusted.
#
# Below this we fall back to the population prior (night) instead of inventing a pattern
# from two data points. Without the floor, a card seen at 10:00 and 11:00 has every other
# hour of its ordinary working day scored "unusual" — which flagged ~17% of legitimate
# traffic and drowned the actual signal. The old linear band had the same hole; it was
# hidden because the simulator put every transaction on one date, so a card's history
# covered its whole active range immediately.
MIN_HOURS_FOR_BAND = 4


def circular_hour_distance(a: int, b: int) -> int:
    """Hours between two clock hours, going the short way round.

    Hours are circular; 23 and 1 are two hours apart, not twenty-two.
    """
    gap = abs(a - b) % 24
    return min(gap, 24 - gap)


def is_unusual_hour(hour: int, historical_hours: Iterable[int] | None) -> bool:
    """Unusual if far from every hour the card is known to transact at.

    Was `min(seen) <= hour <= max(seen)`, which treats a circular quantity as a linear one
    and is wrong for exactly the cards that matter:

        seen {23, 1}, hour 3   -> band [1, 23] swallows the whole night: never unusual
        seen {23, 1}, hour 0   -> 0 is outside [1, 23]: unusual, though it sits between them
        seen {12},    hour 13  -> band [12, 12]: every other hour of the day is "unusual"

    Any card active around midnight had an unusable feature, and a card with one prior
    transaction flagged everything. Measuring the short way round the clock fixes all
    three: distance from the nearest seen hour, `HOUR_TOLERANCE` of slack for hours the
    card simply has not used yet.
    """
    seen = set(historical_hours) if historical_hours is not None else set()
    if len(seen) < MIN_HOURS_FOR_BAND:
        # Too little history to claim a pattern — use the population prior.
        return hour in NIGHT_HOURS
    return min(circular_hour_distance(hour, h) for h in seen) > HOUR_TOLERANCE


def clamp_score(value: float) -> float:
    """Clamp a risk score into [0, 1]."""
    return max(0.0, min(1.0, value))


def clamp_tier(value: int) -> int:
    """Clamp a risk tier into {1..5}."""
    return max(1, min(5, value))


def risk_score(key: object, table: Mapping[object, float], default: float = 0.0) -> float:
    """Look up a merchant/category risk score, clamped to [0, 1]."""
    return clamp_score(table.get(key, default))


def risk_tier(key: object, table: Mapping[object, int], default: int = 2) -> int:
    """Look up a category risk tier, clamped to {1..5}."""
    return clamp_tier(table.get(key, default))
