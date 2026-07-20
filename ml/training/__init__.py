"""Model training layer — XGBoost on the gold training features.

Training logic is locally testable (XGBoost + a local MLflow file store). Full-scale
training on real IEEE-CIS + Databricks MLflow/Feature Store is deferred to deploy.

Every symbol is imported LAZILY, for the same reason `ml.serving` does it. This package's
modules pull numpy, pandas, scikit-learn, xgboost and mlflow, and `ml.training.ingest_ieee`
needs none of them — it uploads a file to S3. Importing them eagerly here meant

    python -m ml.training.ingest_ieee
    ...
    File "ml/training/__init__.py", line 9, in <module>
        from ml.training.dataset import (
    ModuleNotFoundError: No module named 'numpy'

on a deploy runner that had installed exactly what that module declares. The package
`__init__` ran first and died before the module it was asked for ever loaded — the same
failure `ml/serving/__init__.py` documents, one package over.

`from ml.training import train_model` still works wherever the training stack is installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ml.training.dataset import (
        FEATURE_COLUMNS,
        LABEL_COLUMN,
        make_synthetic_frame,
        prepare_xy,
    )
    from ml.training.promote import PromotionDecision, evaluate_promotion
    from ml.training.registry import (
        RegistrationResult,
        register_and_promote,
        target_stage,
        uses_unity_catalog,
    )
    from ml.training.train import (
        TrainConfig,
        TrainingResult,
        split_dataset,
        train_model,
    )

# name -> submodule. Explicit rather than a scan, so a typo raises AttributeError instead of
# quietly importing something unexpected.
_LAZY = {
    "FEATURE_COLUMNS": "dataset",
    "LABEL_COLUMN": "dataset",
    "make_synthetic_frame": "dataset",
    "prepare_xy": "dataset",
    "PromotionDecision": "promote",
    "evaluate_promotion": "promote",
    "RegistrationResult": "registry",
    "register_and_promote": "registry",
    "target_stage": "registry",
    "uses_unity_catalog": "registry",
    "TrainConfig": "train",
    "TrainingResult": "train",
    "split_dataset": "train",
    "train_model": "train",
}

# Spelled out rather than `sorted(_LAZY)`: a computed __all__ is invisible to static
# analysis, so ruff reported all fourteen TYPE_CHECKING imports as unused. A reader loses
# nothing either — the two lists sit next to each other, and the test below fails if they
# drift apart.
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
    "uses_unity_catalog",
]


def __getattr__(name: str) -> object:
    """Resolve a training symbol on first access, not at package import (PEP 562)."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(f"ml.training.{module}"), name)
