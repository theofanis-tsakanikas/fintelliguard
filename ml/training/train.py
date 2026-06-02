"""Pure XGBoost training: deterministic split, train, evaluate, MLflow logging.

Trains on EXACTLY the canonical 15 features (parity asserted; the label is never a
feature). The MLflow tracking URI is configurable — a local file store for tests, a
Databricks URI for real runs. The fraud score AND feature importance are produced for
the downstream Bedrock contract (docs/bedrock-integration.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import mlflow
import mlflow.xgboost
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from ml.training.dataset import FEATURE_COLUMNS, prepare_xy


@dataclass(frozen=True)
class TrainConfig:
    """Training + MLflow configuration."""

    seed: int = 42
    tracking_uri: str = "file:./mlruns"  # local default; Databricks URI for real runs
    experiment: str = "fintelliguard-fraud-xgb"
    registered_model_name: str = "fintelliguard_fraud_xgb"
    test_size: float = 0.15
    val_size: float = 0.15
    threshold: float = 0.5  # decision threshold for precision/recall
    xgb_params: dict[str, Any] = field(
        default_factory=lambda: {
            "n_estimators": 200,
            "max_depth": 5,
            "learning_rate": 0.1,
            "subsample": 1.0,
            "colsample_bytree": 1.0,
            "objective": "binary:logistic",
            "eval_metric": "auc",
            "tree_method": "hist",
            "n_jobs": 1,  # single-threaded -> deterministic
        }
    )


@dataclass(frozen=True)
class Splits:
    x_train: pd.DataFrame
    y_train: pd.Series
    x_val: pd.DataFrame
    y_val: pd.Series
    x_test: pd.DataFrame
    y_test: pd.Series


@dataclass(frozen=True)
class TrainingResult:
    run_id: str
    metrics: dict[str, float]
    feature_importance: dict[str, float]
    model: XGBClassifier


def split_dataset(x: pd.DataFrame, y: pd.Series, config: TrainConfig) -> Splits:
    """Deterministic, stratified train/val/test split driven by `config.seed`."""
    x_temp, x_test, y_temp, y_test = train_test_split(
        x, y, test_size=config.test_size, random_state=config.seed, stratify=y
    )
    val_fraction = config.val_size / (1.0 - config.test_size)
    x_train, x_val, y_train, y_val = train_test_split(
        x_temp, y_temp, test_size=val_fraction, random_state=config.seed, stratify=y_temp
    )
    return Splits(x_train, y_train, x_val, y_val, x_test, y_test)


def _evaluate(
    model: XGBClassifier, x_test: pd.DataFrame, y_test: pd.Series, threshold: float
) -> dict[str, float]:
    proba = model.predict_proba(x_test)[:, 1]
    pred = (proba >= threshold).astype(int)
    return {
        "auc_roc": float(roc_auc_score(y_test, proba)),
        "pr_auc": float(average_precision_score(y_test, proba)),
        "fraud_precision": float(precision_score(y_test, pred, pos_label=1, zero_division=0)),
        "fraud_recall": float(recall_score(y_test, pred, pos_label=1, zero_division=0)),
    }


def train_model(frame: pd.DataFrame, config: TrainConfig | None = None) -> TrainingResult:
    """Train + evaluate + log one model from an injected features+label frame."""
    config = config or TrainConfig()

    x, y = prepare_xy(frame)
    # Parity guard: train on exactly the canonical 15, label excluded.
    if list(x.columns) != FEATURE_COLUMNS:
        raise ValueError("training features differ from the canonical schema")

    splits = split_dataset(x, y, config)
    model = XGBClassifier(random_state=config.seed, **config.xgb_params)
    model.fit(
        splits.x_train, splits.y_train, eval_set=[(splits.x_val, splits.y_val)], verbose=False
    )

    metrics = _evaluate(model, splits.x_test, splits.y_test, config.threshold)
    importance = {
        name: float(score) for name, score in zip(FEATURE_COLUMNS, model.feature_importances_)
    }

    mlflow.set_tracking_uri(config.tracking_uri)
    mlflow.set_experiment(config.experiment)
    with mlflow.start_run() as run:
        mlflow.log_params(
            {
                "seed": config.seed,
                "n_features": len(FEATURE_COLUMNS),
                "n_train": len(splits.x_train),
                "n_test": len(splits.x_test),
                **{f"xgb_{k}": v for k, v in config.xgb_params.items()},
            }
        )
        mlflow.log_metrics(metrics)
        mlflow.log_dict(importance, "feature_importance.json")
        mlflow.xgboost.log_model(model, artifact_path="model")
        run_id = run.info.run_id

    return TrainingResult(
        run_id=run_id, metrics=metrics, feature_importance=importance, model=model
    )
