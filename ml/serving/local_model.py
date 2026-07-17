"""Bootstrap a demo fraud scorer from the simulator — no external dataset, no cloud.

The real training path (`ml/training/`) fits on IEEE-CIS in Databricks. For the local
end-to-end demo we need a model NOW, self-contained: generate deterministic labelled
transactions from the simulator, build the canonical 15 features with the SAME
`adapter_stream` the Gold layer uses (feature parity preserved), and fit an XGBoost
`FraudScorer`. So the local service scores with the real model + TreeSHAP, not a stub.

This is the local analogue of the (deferred) IEEE-CIS bootstrap: a one-call, deterministic
way to get a scoring model from data the repo already produces.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import islice

import pandas as pd
from xgboost import XGBClassifier

from ml.features.adapter_stream import compute_features
from ml.features.merchant_risk import MerchantRiskTable, build_merchant_risk_table
from ml.features.schema import FEATURE_NAMES
from ml.serving.scorer import FraudScorer, ScoringConfig
from simulator.config import SimulatorConfig
from simulator.generator import TransactionGenerator

# Fraction of the generated stream reserved for fitting the merchant risk table. These
# transactions are NOT used as training rows: the table is a target encoding, so fitting it
# on rows it will later encode leaks the label into the feature.
TABLE_FIT_FRACTION = 0.3


@dataclass(frozen=True)
class DemoModel:
    """The scorer and the feature artefacts it must be served with.

    The merchant risk table is part of the model's feature contract, not configuration —
    a scorer served against a different table than it was trained on is train/serve skew.
    They travel together so a caller cannot hold one without the other.
    """

    scorer: FraudScorer
    merchant_risk_table: MerchantRiskTable
    holdout_auc: float


def _generate(max_records: int, fraud_rate: float, seed: int) -> list[tuple[dict, int, datetime]]:
    config = SimulatorConfig(
        rate_per_sec=500.0, max_records=max_records, fraud_injection_rate=fraud_rate, seed=seed
    )
    out = []
    # `stream()` yields forever (max_records is the runner's job) — bound it here.
    for txn in islice(TransactionGenerator(config).stream(), max_records):
        contract = txn.to_contract_dict()
        out.append(
            (contract, int(bool(txn.is_fraud_truth)), datetime.fromisoformat(contract["timestamp"]))
        )
    out.sort(key=lambda item: item[2])
    return out


def generate_labeled_dataset(
    *, max_records: int = 2000, fraud_rate: float = 0.18, seed: int = 42
) -> tuple[pd.DataFrame, pd.Series, MerchantRiskTable]:
    """Deterministic (X, y, merchant_risk_table) for the local demo model.

    A higher-than-production fraud rate (default 18%) is used only so the tiny demo model
    sees enough positives to learn a usable decision surface — the live stream keeps the
    realistic ~1%.

    The stream is split in time. The earliest `TABLE_FIT_FRACTION` fits the merchant risk
    table and then serves only as history; the rest becomes training rows. Everything a row
    sees is strictly older than the row itself.
    """
    events = _generate(max_records, fraud_rate, seed)
    split = int(len(events) * TABLE_FIT_FRACTION)
    fit_period, train_period = events[:split], events[split:]

    table = build_merchant_risk_table(
        [
            {"merchant_id": contract["merchant_id"], "is_fraud": label}
            for contract, label, _ in fit_period
        ]
    )

    history: dict[str, list[dict]] = defaultdict(list)
    first_seen: dict[str, datetime] = {}
    for contract, _, when in fit_period:
        card = contract["card_hash"]
        history[card].append(contract)
        first_seen.setdefault(card, when)

    rows: list[dict] = []
    labels: list[int] = []
    for contract, label, when in train_period:
        card = contract["card_hash"]
        first_seen.setdefault(card, when)
        record = compute_features(
            contract,
            history[card],
            merchant_risk_table=table,
            card_first_seen=first_seen[card],
        )
        rows.append(record.features.as_dict())
        labels.append(label)
        history[card].append(contract)

    features = pd.DataFrame(rows, columns=list(FEATURE_NAMES)).astype(float)
    return features, pd.Series(labels, dtype=int), table


def train_demo_scorer(
    *, seed: int = 42, config: ScoringConfig | None = None, **data_kwargs: object
) -> DemoModel:
    """Generate data, fit a small deterministic XGBoost, and report held-out AUC.

    The fit used to be on 100% of the rows with no held-out split at all, so nothing this
    model produced could distinguish a working scorer from a memorised one — and the local
    funnel's `decision_hint` bands were applied to its probabilities regardless.
    """
    features, labels, table = generate_labeled_dataset(seed=seed, **data_kwargs)  # type: ignore[arg-type]

    # Time-ordered split: the rows are already in event order, so the tail is the future.
    cut = int(len(features) * 0.8)
    x_train, x_test = features.iloc[:cut], features.iloc[cut:]
    y_train, y_test = labels.iloc[:cut], labels.iloc[cut:]

    model = XGBClassifier(
        n_estimators=150,
        max_depth=4,
        learning_rate=0.2,
        subsample=0.9,
        colsample_bytree=0.9,
        n_jobs=1,
        random_state=seed,
        eval_metric="logloss",
    )
    model.fit(x_train, y_train)

    auc = _holdout_auc(model, x_test, y_test)
    return DemoModel(
        scorer=FraudScorer(model, config or ScoringConfig()),
        merchant_risk_table=table,
        holdout_auc=auc,
    )


def _holdout_auc(model: XGBClassifier, x_test: pd.DataFrame, y_test: pd.Series) -> float:
    """AUC on the held-out tail; 0.5 (i.e. no information) when it has one class only."""
    from sklearn.metrics import roc_auc_score

    if y_test.nunique() < 2:
        return 0.5
    return float(roc_auc_score(y_test, model.predict_proba(x_test)[:, 1]))
