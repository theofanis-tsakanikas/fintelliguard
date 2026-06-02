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

# ProductCD -> historical fraud rate proxy (merchant_risk_score) and risk tier.
PRODUCTCD_RISK_SCORE: dict[str, float] = {"W": 0.02, "C": 0.12, "R": 0.05, "H": 0.08, "S": 0.10}
PRODUCTCD_RISK_TIER: dict[str, int] = {"W": 1, "R": 2, "H": 3, "S": 4, "C": 5}


@dataclass(frozen=True)
class CardContext:
    """Per-`card1` aggregates a batch job derives, supplied to the row mapper."""

    amount_mean: float = 0.0
    amount_std: float = 0.0
    modal_addr2: float | None = None  # the card's usual billing country code
    seconds_per_day: int = 86_400


def _num(row: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def map_row(row: Mapping[str, Any], context: CardContext | None = None) -> FeatureRecord:
    """Map one IEEE-CIS row to the canonical 15 features.

    Proxy columns (see docs/features.md): C1/C2/C4/C6 (counts), D1 (days since first
    seen), addr2 (country), dist1 (distance), ProductCD (merchant), TransactionDT (time).
    """
    ctx = context or CardContext()
    amount = _num(row, "TransactionAmt")

    # Amount: TransactionAmt is already USD; z-score vs the card1 group mean/std.
    amount_zscore = transforms.zscore(amount, ctx.amount_mean, ctx.amount_std)

    # Velocity: C-counts are pre-computed counts. Clamp 24h >= 1h to honour the gate.
    velocity_1h = int(_num(row, "C1"))
    velocity_24h = max(int(_num(row, "C2")), velocity_1h)
    amount_sum_1h = velocity_1h * amount  # proxy: C1 x amount (docs/features.md #6)
    distinct_merchants_24h = int(_num(row, "C4"))

    # Identity & device.
    card_age_days = int(_num(row, "D1"))  # D1 = days since first transaction
    # Proxy: a positive days-since-first-seen implies prior card/device activity;
    # IEEE-CIS has no per-device history, so this is an honest approximation.
    device_seen_before = card_age_days > 0
    device_txn_count_24h = int(_num(row, "C6"))

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
    hour = int((_num(row, "TransactionDT") // 3600) % 24)
    is_unusual_hour = transforms.is_unusual_hour(hour, None)

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
