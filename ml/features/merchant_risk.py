"""The merchant risk table — the lookup `docs/features.md` promised and nothing built.

`merchant_risk_score` is one of the 15 features. `compute_features` accepted the table as
an optional keyword defaulting to `None -> {}`, and **no production caller ever passed
it**:

    pipelines/gold/gold_transforms.py:76   compute_features(current, history)
    ml/serving/stream_service.py:98        compute_features(contract, history.get(card))
    ml/serving/local_model.py:47           compute_features(contract, history[card])

So `risk_score(merchant_id, {}, default=0.0)` returned 0.0 for every row in
`gold.txn_features_realtime` and every transaction the scorer ever scored, while the IEEE
training adapter emitted 0.02-0.12 from `PRODUCTCD_RISK_SCORE`. The model learned splits on
a feature that is pinned to a constant outside the training support at inference. `docs/
features.md` described "lookup in merchant risk table (Gold)"; no such table existed
anywhere in the repo.

An optional parameter with a neutral default is how a feature dies quietly: nothing fails,
nothing warns, and the score is merely wrong. So `compute_features` now REQUIRES the table
and this module is where it comes from.

The score is a smoothed historical fraud rate per merchant. Smoothing matters: a merchant
with one transaction that happened to be fraud is not a 100%-risk merchant, and without a
prior it would dominate the split. `PRIOR_WEIGHT` transactions of the population base rate
are added to every merchant, so evidence has to accumulate before a merchant moves.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

# Pseudo-transactions of the population base rate mixed into every merchant. A merchant
# needs materially more than this many observations before its own rate dominates.
PRIOR_WEIGHT = 50.0

# Used when the table has no entry for a merchant — a first-seen merchant is an unknown,
# not a safe one, so it inherits the population base rate rather than 0.0.
DEFAULT_BASE_RATE = 0.02


class MerchantRiskTable(dict):
    """Merchant id -> smoothed fraud rate, plus the base rate for unseen merchants.

    A dict subclass so it drops straight into `transforms.risk_score`, which does
    `table.get(key, default)`.
    """

    def __init__(self, scores: Mapping[str, float], base_rate: float = DEFAULT_BASE_RATE):
        super().__init__(scores)
        self.base_rate = base_rate

    def score(self, merchant_id: object) -> float:
        """The merchant's rate, or the population base rate if it has never been seen."""
        return float(self.get(merchant_id, self.base_rate))


def build_merchant_risk_table(
    rows: Iterable[Mapping[str, Any]],
    *,
    merchant_key: str = "merchant_id",
    label_key: str = "is_fraud",
    prior_weight: float = PRIOR_WEIGHT,
) -> MerchantRiskTable:
    """Smoothed fraud rate per merchant, from labelled historical transactions.

    Must be built from data STRICTLY OLDER than anything it will score. It is a target
    encoding: fit it on the rows you are about to score and the label leaks into the
    feature, which is a far more expensive bug than the dead constant it replaces.
    `ml/training/train.py` builds it from the training split only.
    """
    totals: dict[str, list[float]] = {}
    fraud_count = 0.0
    total_count = 0.0

    for row in rows:
        merchant = str(row[merchant_key])
        label = 1.0 if row[label_key] else 0.0
        bucket = totals.setdefault(merchant, [0.0, 0.0])
        bucket[0] += label
        bucket[1] += 1.0
        fraud_count += label
        total_count += 1.0

    base_rate = fraud_count / total_count if total_count else DEFAULT_BASE_RATE

    scores = {
        merchant: (frauds + prior_weight * base_rate) / (count + prior_weight)
        for merchant, (frauds, count) in totals.items()
    }
    return MerchantRiskTable(scores, base_rate=base_rate)


def save(table: MerchantRiskTable, path: Path) -> None:
    """Persist the table beside the model that was trained with it.

    The table is part of the model's feature contract, not a config file: a model scored
    against a table built from different data is train/serve skew of the subtlest kind, so
    the two travel together.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"base_rate": table.base_rate, "scores": dict(table)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load(path: Path) -> MerchantRiskTable:
    """Read a persisted table. Raises rather than returning an empty one.

    Fail-closed on purpose. An empty table scores every merchant at the base rate, which
    looks like a working system and is exactly the failure this module exists to end.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"no merchant risk table at {path} — the scorer cannot compute "
            "merchant_risk_score without it, and defaulting it to a constant is the bug "
            "this file was written to fix"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return MerchantRiskTable(payload["scores"], base_rate=payload["base_rate"])
