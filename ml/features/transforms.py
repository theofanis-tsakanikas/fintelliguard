"""Shared, source-independent feature transforms.

Both adapters call these so source-independent features cannot diverge between training
(IEEE) and serving (stream). Pure functions only — no Spark, no IO.
"""

from __future__ import annotations

import math
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


def values_differ(value: object, reference: object | None) -> bool:
    """True if `value` differs from a known `reference`; False when reference is unknown."""
    if reference is None:
        return False
    return value != reference


def is_unusual_hour(hour: int, historical_hours: Iterable[int] | None) -> bool:
    """Unusual if outside the card's active hour band; night-heuristic when no history.

    Using the [min, max] band of seen hours (rather than the exact seen set) avoids
    flagging normal daytime activity at an hour the card simply hasn't used yet, while
    still catching nocturnal fraud well outside the band.
    """
    seen = set(historical_hours) if historical_hours is not None else set()
    if seen:
        return not (min(seen) <= hour <= max(seen))
    return hour in NIGHT_HOURS


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
