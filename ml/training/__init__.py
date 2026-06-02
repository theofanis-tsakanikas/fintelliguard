"""Model training layer — XGBoost on the gold training features.

Training logic is locally testable (XGBoost + a local MLflow file store). Full-scale
training on real IEEE-CIS + Databricks MLflow/Feature Store is deferred to deploy.
"""

from __future__ import annotations

from ml.training.dataset import (
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    make_synthetic_frame,
    prepare_xy,
)
from ml.training.promote import PromotionDecision, evaluate_promotion
from ml.training.registry import RegistrationResult, register_and_promote, target_stage
from ml.training.train import TrainConfig, TrainingResult, split_dataset, train_model

__all__ = [
    "FEATURE_COLUMNS",
    "LABEL_COLUMN",
    "PromotionDecision",
    "RegistrationResult",
    "TrainConfig",
    "TrainingResult",
    "evaluate_promotion",
    "make_synthetic_frame",
    "prepare_xy",
    "register_and_promote",
    "split_dataset",
    "target_stage",
    "train_model",
]
