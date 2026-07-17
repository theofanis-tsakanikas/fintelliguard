"""What the 15 features MEAN — the missing half of the feature contract.

`schema.py` pins names, types and ranges. Both adapters satisfied it completely while
disagreeing about what the numbers meant, and the parity test could not see it because it
compared dataclass field types, which cannot diverge. Six skews lived underneath:

    feature                  stream                    IEEE (training)
    txn_velocity_1h          len(within_1h) + 1        int(C1)          -> min 1 vs min 0
    txn_velocity_24h         len(within_24h) + 1       max(C2, C1)      -> min 1 vs min 0
    distinct_merchants_24h   len(prior | {current})    int(C4)          -> min 1 vs min 0
    device_txn_count_24h     sum(...) + 1              int(C6)          -> min 1 vs min 0
    amount_sum_1h            amount + sum(prior)       C1 * amount      -> 0.0 while
                                                                           amount_usd = 120

A seventh, `merchant_risk_score`, was removed from the contract entirely rather than
reconciled: it is a target encoding, and the labels and the merchant identities live in
different datasets, so no definition of it can be identical on both sides. See
`ml/features/schema.py`.

An XGBoost split learned at `txn_velocity_1h <= 0.5` — the IEEE "no prior activity" bucket
— is unreachable at serving, where the minimum is 1. That is not a rounding difference; it
is a branch of the model that never executes in production.

This module states the semantics once, as executable invariants that BOTH adapters are
checked against independently. A feature whose meaning cannot be written down as an
invariant here does not belong in the contract.

The window convention, stated once and for all: **every window count and window sum
INCLUDES the current transaction.** A transaction is an event in its own 1-hour window.
So `txn_velocity_1h >= 1` always, and `amount_sum_1h >= amount_usd` always.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .schema import FeatureVector

# The current transaction is always a member of its own trailing window.
MIN_WINDOW_COUNT = 1


@dataclass(frozen=True)
class Invariant:
    """A statement about a feature vector that must hold for EVERY adapter.

    `why` is not decoration. It is the reason the invariant survives the next person who
    finds it inconvenient — it names the skew that motivated it.
    """

    name: str
    holds: Callable[[FeatureVector], bool]
    why: str

    def check(self, features: FeatureVector) -> str | None:
        return None if self.holds(features) else f"{self.name}: {self.why}"


INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "velocity_1h_counts_current",
        lambda f: f.txn_velocity_1h >= MIN_WINDOW_COUNT,
        "the current transaction is in its own 1h window, so the count is never 0 — "
        "IEEE emitted int(C1) with a floor of 0 while the stream emitted len(prior)+1, "
        "shifting every learned split by one",
    ),
    Invariant(
        "velocity_24h_counts_current",
        lambda f: f.txn_velocity_24h >= MIN_WINDOW_COUNT,
        "same window convention as 1h",
    ),
    Invariant(
        "velocity_24h_dominates_1h",
        lambda f: f.txn_velocity_24h >= f.txn_velocity_1h,
        "the 24h window contains the 1h window",
    ),
    Invariant(
        "amount_sum_1h_includes_current",
        lambda f: f.amount_sum_1h >= f.amount_usd,
        "the 1h amount window contains the current amount, so the sum can never be less "
        "than it — IEEE's C1*amount proxy emitted 0.0 for a 120.0 transaction, a state "
        "the serving path cannot produce",
    ),
    Invariant(
        "distinct_merchants_counts_current",
        lambda f: f.distinct_merchants_24h >= MIN_WINDOW_COUNT,
        "the current merchant is one of the distinct merchants in the window",
    ),
    Invariant(
        "device_txn_count_counts_current",
        lambda f: f.device_txn_count_24h >= MIN_WINDOW_COUNT,
        "the current transaction used the current device",
    ),
    Invariant(
        "distinct_countries_counts_current",
        lambda f: f.distinct_countries_24h >= MIN_WINDOW_COUNT,
        "the current transaction has a country",
    ),
)


def check_invariants(features: FeatureVector) -> list[str]:
    """Every semantic invariant `features` violates. Empty means the vector is coherent."""
    return [msg for inv in INVARIANTS if (msg := inv.check(features)) is not None]


def assert_invariants(features: FeatureVector) -> None:
    """Raise ValueError naming every semantic invariant `features` violates."""
    if violations := check_invariants(features):
        raise ValueError("; ".join(violations))
