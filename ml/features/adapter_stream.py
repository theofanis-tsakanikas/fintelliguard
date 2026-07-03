"""Stream adapter: the 15 canonical features from the simulator's bronze contract.

The window/state features (velocities, distinct counts, card age, device-seen, country
mismatch, unusual hour) are PURE functions over a per-card history passed in as a plain
list of prior contract dicts — NO Spark here. `pipelines/gold` will supply that history
via Spark Structured Streaming (`flatMapGroupsWithState` + sliding windows) and call
these functions, so the logic is identical and testable today.

No target leakage: only transactions strictly BEFORE the current one are used. The
history is defensively filtered by timestamp, so a caller cannot leak the future.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from . import transforms
from .schema import FeatureRecord, FeatureVector

# MCC -> risk tier (1..5). Stream-source data; the lookup/clamp logic is shared.
MCC_RISK_TIERS: dict[str, int] = {
    "5411": 1,  # grocery
    "4111": 1,  # transit
    "5812": 2,  # restaurants
    "5912": 2,  # pharmacies
    "5999": 2,  # misc retail
    "4814": 3,  # telecom
    "5732": 3,  # electronics
    "5944": 4,  # jewelry
    "6011": 4,  # ATM / cash
    "7995": 5,  # gambling
}

_ONE_HOUR = timedelta(hours=1)
_ONE_DAY = timedelta(hours=24)
_THIRTY_DAYS = timedelta(days=30)


class FeatureComputationError(ValueError):
    """A transaction cannot be turned into features (e.g. an unparseable timestamp).

    Raised for the CURRENT transaction so the caller (the streaming service / the Gold
    ``applyInPandas`` batch) quarantines that one row instead of crashing the whole card
    group. Silver's ``valid_event_time`` gate is the primary defence; this is defence in
    depth for rescued/malformed rows that slip through.
    """


def _parse(ts: object) -> datetime:
    """Parse an ISO-8601 timestamp, raising a typed, diagnosable error on bad input."""
    try:
        return datetime.fromisoformat(str(ts))
    except (ValueError, TypeError) as exc:
        raise FeatureComputationError(f"invalid ISO-8601 timestamp: {ts!r}") from exc


def _parse_or_none(ts: object) -> datetime | None:
    """Parse a HISTORY timestamp, returning None on bad input (skip, never crash the group)."""
    try:
        return _parse(ts)
    except FeatureComputationError:
        return None


def compute_features(
    current: Mapping[str, Any],
    card_history: Sequence[Mapping[str, Any]],
    *,
    merchant_risk_table: Mapping[str, float] | None = None,
) -> FeatureRecord:
    """Compute the canonical 15 features for `current` given the card's prior events.

    `current` and each history item are bronze contract dicts (transaction_id, timestamp,
    amount, merchant_id, card_hash, device_id, ip_country, mcc_code).
    """
    merchant_risk_table = merchant_risk_table or {}
    # A bad/missing CURRENT timestamp raises FeatureComputationError -> caller quarantines.
    now = _parse(current.get("timestamp"))
    amount = float(current["amount"])

    # Parse each history timestamp ONCE (was re-parsed ~5x per row), skipping unparseable
    # rows defensively. Strictly-before-now only — the no-leakage guarantee.
    prior_ts = [
        (h, ts)
        for h in card_history
        if (ts := _parse_or_none(h.get("timestamp"))) is not None and ts < now
    ]
    prior = [h for h, _ in prior_ts]
    within_1h = [h for h, ts in prior_ts if now - ts <= _ONE_HOUR]
    within_24h = [h for h, ts in prior_ts if now - ts <= _ONE_DAY]
    within_30d = [h for h, ts in prior_ts if now - ts <= _THIRTY_DAYS]

    # Amount features.
    hist_amounts = [float(h["amount"]) for h in within_30d]
    mean = sum(hist_amounts) / len(hist_amounts) if hist_amounts else amount
    std = _stddev(hist_amounts, mean)

    # Velocity features (count the current txn as well; bounded windows keep them honest).
    velocity_1h = len(within_1h) + 1
    velocity_24h = len(within_24h) + 1
    amount_sum_1h = amount + sum(float(h["amount"]) for h in within_1h)
    distinct_merchants = len({h["merchant_id"] for h in within_24h} | {current["merchant_id"]})

    # Identity & device.
    first_seen = min((ts for _, ts in prior_ts), default=now)
    card_age_days = (now - first_seen).days
    device_seen_before = any(h["device_id"] == current["device_id"] for h in prior)
    device_txn_count_24h = sum(1 for h in within_24h if h["device_id"] == current["device_id"]) + 1

    # Geography.
    modal_country = _modal(h["ip_country"] for h in prior)
    country_mismatch = transforms.values_differ(current["ip_country"], modal_country)
    distinct_countries = len({h["ip_country"] for h in within_24h} | {current["ip_country"]})

    # Merchant.
    merchant_risk = transforms.risk_score(current["merchant_id"], merchant_risk_table, default=0.0)
    mcc_tier = transforms.risk_tier(current["mcc_code"], MCC_RISK_TIERS, default=2)

    # Temporal.
    historical_hours = [ts.hour for _, ts in prior_ts if now - ts <= _THIRTY_DAYS]
    unusual_hour = transforms.is_unusual_hour(now.hour, historical_hours)

    features = FeatureVector(
        amount_usd=amount,
        amount_log=transforms.amount_log(amount),
        amount_zscore=transforms.zscore(amount, mean, std),
        txn_velocity_1h=velocity_1h,
        txn_velocity_24h=velocity_24h,
        amount_sum_1h=amount_sum_1h,
        distinct_merchants_24h=distinct_merchants,
        card_age_days=card_age_days,
        device_seen_before=device_seen_before,
        device_txn_count_24h=device_txn_count_24h,
        country_mismatch=country_mismatch,
        distinct_countries_24h=distinct_countries,
        merchant_risk_score=merchant_risk,
        mcc_risk_tier=mcc_tier,
        is_unusual_hour=unusual_hour,
    )
    return FeatureRecord(
        transaction_id=str(current["transaction_id"]),
        card_hash=str(current["card_hash"]),
        features=features,
    )


def _stddev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance**0.5


def _modal(values: Iterable[str]) -> str | None:
    counts = Counter(values)
    if not counts:
        return None
    return counts.most_common(1)[0][0]
