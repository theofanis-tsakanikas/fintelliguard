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
from itertools import islice

import pandas as pd
from xgboost import XGBClassifier

from ml.features.adapter_stream import compute_features
from ml.features.schema import FEATURE_NAMES
from ml.serving.scorer import FraudScorer, ScoringConfig
from simulator.config import SimulatorConfig
from simulator.generator import TransactionGenerator


def generate_labeled_dataset(
    *, max_records: int = 2000, fraud_rate: float = 0.18, seed: int = 42
) -> tuple[pd.DataFrame, pd.Series]:
    """Deterministic (X, y): the 15 canonical features + the ground-truth fraud label.

    A higher-than-production fraud rate (default 18%) is used only so the tiny demo model
    sees enough positives to learn a usable decision surface — the live stream keeps the
    realistic ~1%.
    """
    config = SimulatorConfig(
        rate_per_sec=500.0, max_records=max_records, fraud_injection_rate=fraud_rate, seed=seed
    )
    history: dict[str, list[dict]] = defaultdict(list)
    rows: list[dict] = []
    labels: list[int] = []
    # `stream()` yields forever (max_records is the runner's job) — bound it here.
    for txn in islice(TransactionGenerator(config).stream(), max_records):
        contract = txn.to_contract_dict()
        card = contract["card_hash"]
        record = compute_features(contract, history[card])
        rows.append(record.features.as_dict())
        labels.append(int(bool(txn.is_fraud_truth)))
        history[card].append(contract)

    features = pd.DataFrame(rows, columns=list(FEATURE_NAMES)).astype(float)
    return features, pd.Series(labels, dtype=int)


def train_demo_scorer(
    *, seed: int = 42, config: ScoringConfig | None = None, **data_kwargs: object
) -> FraudScorer:
    """Generate data, fit a small deterministic XGBoost, and wrap it in the scorer contract."""
    features, labels = generate_labeled_dataset(seed=seed, **data_kwargs)  # type: ignore[arg-type]
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
    model.fit(features, labels)
    return FraudScorer(model, config or ScoringConfig())
