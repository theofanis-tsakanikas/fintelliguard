"""IEEE-CIS adapter: maps IEEE-CIS columns to the SAME canonical 15 features.

Where IEEE-CIS has no 1:1 equivalent we use the documented proxies from
`docs/features.md` and note each one inline. The goal is parity of *semantics*, not a
perfect mapping — what matters is that training (IEEE) and serving (stream) emit the same
feature contract.

Per-card aggregates that a real batch job computes with a group-by on `card1`
(amount mean/std, modal address, ...) are passed in as `CardContext`; without it the
context-dependent features fall back to neutral values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import transforms
from .schema import FeatureRecord, FeatureVector
from .semantics import MIN_WINDOW_COUNT

# ProductCD -> historical fraud rate proxy (merchant_risk_score) and risk tier.
PRODUCTCD_RISK_SCORE: dict[str, float] = {"W": 0.02, "C": 0.12, "R": 0.05, "H": 0.08, "S": 0.10}
PRODUCTCD_RISK_TIER: dict[str, int] = {"W": 1, "R": 2, "H": 3, "S": 4, "C": 5}


@dataclass(frozen=True)
class CardContext:
    """Per-`card1` aggregates a batch job derives, supplied to the row mapper.

    These must be computed from transactions STRICTLY BEFORE the row being mapped. The
    Gold training path used to compute them over the whole card group — future rows
    included — which is the target leakage `adapter_stream`'s docstring promises does not
    exist. `pipelines/gold/gold_transforms.py` now walks the group in time order.
    """

    amount_mean: float = 0.0
    amount_std: float = 0.0
    modal_addr2: object | None = None  # the card's usual billing country code
    # Hours this card is known to transact at. Without it the IEEE side silently took the
    # night heuristic on EVERY row while the stream side took the active-band branch — one
    # feature, two different functions.
    active_hours: tuple[int, ...] | None = None
    # Whether this transaction's device has been seen on this card before. IEEE-CIS carries
    # device identity in the `id_*`/`DeviceInfo` columns, which a batch job resolves; the
    # adapter must not invent it. When absent we fall back to C6 (see `map_row`) rather
    # than to `card_age_days > 0`, which made the feature a duplicate carrying no signal.
    device_seen_before: bool | None = None
    seconds_per_day: int = 86_400


def _num(row: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    """A numeric cell, or `default` when it is missing or unusable.

    NaN is explicitly a miss. It was not: `float('nan')` passed the try/except cleanly, and
    the `int(...)` calls below then raised `ValueError: cannot convert float NaN to
    integer`, killing the whole `applyInPandas` task for the card group. Silver's
    `coalesce` does not help — it replaces NULL, and NaN is not NULL. IEEE-CIS's C/D/dist
    columns are famously sparse, so this was reachable with real data.
    """
    value = row.get(key)
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else number  # NaN != NaN


def map_row(row: Mapping[str, Any], context: CardContext | None = None) -> FeatureRecord:
    """Map one IEEE-CIS row to the canonical 15 features.

    Proxy columns (see docs/features.md): C1/C2/C4/C6 (counts), D1 (days since first
    seen), addr2 (country), dist1 (distance), ProductCD (merchant), TransactionDT (time).
    """
    ctx = context or CardContext()
    amount = _num(row, "TransactionAmt")

    # Amount: TransactionAmt is already USD; z-score vs the card1 group mean/std.
    amount_zscore = transforms.zscore(amount, ctx.amount_mean, ctx.amount_std)

    # Velocity: C-counts are pre-computed counts.
    #
    # The floors are the fix for a systematic off-by-one. `ml/features/semantics.py` fixes
    # ONE window convention — every count includes the current transaction — and the stream
    # adapter honours it (`len(within_1h) + 1`). Raw C-counts bottom out at 0, so `int(C1)`
    # trained the model on a "no activity at all" bucket that the serving path, with its
    # floor of 1, can never produce. A split learned at `txn_velocity_1h <= 0.5` was dead
    # code in production.
    velocity_1h = max(int(_num(row, "C1")), MIN_WINDOW_COUNT)
    velocity_24h = max(int(_num(row, "C2")), velocity_1h)
    distinct_merchants_24h = max(int(_num(row, "C4")), MIN_WINDOW_COUNT)
    device_txn_count_24h = max(int(_num(row, "C6")), MIN_WINDOW_COUNT)

    # Proxy (docs/features.md #6): the current amount plus the prior in-window transactions
    # valued at the card's mean. IEEE-CIS has no true 1h amount sum.
    #
    # Was `velocity_1h * amount`, which produced 0.0 for a 120.0 transaction whenever
    # C1 was 0 — a state the stream path cannot reach, since its sum always contains the
    # current amount. Building from `amount` upward makes the invariant
    # `amount_sum_1h >= amount_usd` true by construction on both sides.
    amount_sum_1h = amount + (velocity_1h - MIN_WINDOW_COUNT) * ctx.amount_mean

    # Identity & device.
    card_age_days = int(_num(row, "D1"))  # D1 = days since first transaction
    # Device history comes from the batch job (the `id_*`/`DeviceInfo` columns), not from
    # a guess. Was `card_age_days > 0`, which made this feature a deterministic function of
    # feature #8: zero independent signal at training, so the model never learned the
    # new-device signature the simulator injects as a fraud archetype. Falling back to C6
    # ("this device transacted in the last 24h") keeps it a device fact rather than a
    # shadow of card age.
    if ctx.device_seen_before is not None:
        device_seen_before = ctx.device_seen_before
    else:
        device_seen_before = device_txn_count_24h > MIN_WINDOW_COUNT

    # Geography: addr2 is the billing country code; mismatch vs the card's modal addr2.
    addr2 = row.get("addr2")
    country_mismatch = (
        transforms.values_differ(addr2, ctx.modal_addr2) if addr2 is not None else False
    )
    # Proxy: dist1 > 0 (transaction far from billing) suggests a second location.
    dist1 = _num(row, "dist1")
    distinct_countries_24h = 2 if dist1 > 0 else 1

    # Merchant: fraud rate / tier per ProductCD.
    product_cd = str(row.get("ProductCD", ""))
    merchant_risk_score = transforms.risk_score(product_cd, PRODUCTCD_RISK_SCORE, default=0.02)
    mcc_risk_tier = transforms.risk_tier(product_cd, PRODUCTCD_RISK_TIER, default=2)

    # Temporal: hour-of-day from TransactionDT (seconds offset from a reference).
    #
    # Passing the card's known active hours puts this on the SAME branch of the same
    # function as the stream adapter. It used to pass None unconditionally, so IEEE always
    # took the night heuristic while the stream always took the active-band branch: one
    # feature name, two different definitions, on either side of the train/serve boundary.
    hour = int((_num(row, "TransactionDT") // 3600) % 24)
    is_unusual_hour = transforms.is_unusual_hour(hour, ctx.active_hours)

    features = FeatureVector(
        amount_usd=amount,
        amount_log=transforms.amount_log(amount),
        amount_zscore=amount_zscore,
        txn_velocity_1h=velocity_1h,
        txn_velocity_24h=velocity_24h,
        amount_sum_1h=amount_sum_1h,
        distinct_merchants_24h=distinct_merchants_24h,
        card_age_days=card_age_days,
        device_seen_before=device_seen_before,
        device_txn_count_24h=device_txn_count_24h,
        country_mismatch=country_mismatch,
        distinct_countries_24h=distinct_countries_24h,
        merchant_risk_score=merchant_risk_score,
        mcc_risk_tier=mcc_risk_tier,
        is_unusual_hour=is_unusual_hour,
    )
    return FeatureRecord(
        transaction_id=str(row["TransactionID"]),
        card_hash=str(row["card1"]),  # card1 is the closest stable card identity proxy
        features=features,
    )
