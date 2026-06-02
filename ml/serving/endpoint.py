"""MLflow pyfunc wrapper — the deployable artifact for Mosaic AI Model Serving.

The pyfunc owns a trained XGBoost model + a `FraudScorer` and returns the
`get_fraud_score()` contract per input row. Features arrive as a DataFrame (injected) so
scoring is testable locally.

In production, the Model Serving endpoint fetches online features from the Feature Store
by lookup key (`card_hash`) before scoring — **the actual Model Serving deployment and the
online Feature Store lookup are deferred to the deploy phase**. Here we ship a loadable,
predict-able artifact.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import mlflow
import mlflow.pyfunc
import mlflow.xgboost
import pandas as pd

from ml.serving.scorer import FraudScorer, ScoringConfig

_XGB_ARTIFACT = "xgb_model"


class FraudScoringModel(mlflow.pyfunc.PythonModel):
    """pyfunc model: loads the XGBoost artifact and scores via `FraudScorer`."""

    def __init__(self, config: ScoringConfig | None = None) -> None:
        self._config = config or ScoringConfig()
        self._scorer: FraudScorer | None = None

    def load_context(self, context: Any) -> None:
        model = mlflow.xgboost.load_model(context.artifacts[_XGB_ARTIFACT])
        self._scorer = FraudScorer(model, self._config)

    def predict(
        self, context: Any, model_input: pd.DataFrame, params: Any = None
    ) -> list[dict[str, Any]]:
        if self._scorer is None:  # pragma: no cover - load_context always runs first in serving
            self.load_context(context)
        return [self._scorer.score(row) for row in model_input.to_dict("records")]


def log_scoring_model(
    xgb_model: Any,
    *,
    config: ScoringConfig | None = None,
    artifact_path: str = "fraud_scorer",
    registered_model_name: str | None = None,
) -> str:
    """Log the pyfunc scoring model to MLflow; return its model URI.

    Caller sets the tracking URI (local file store for tests, Databricks for real).
    """
    config = config or ScoringConfig()
    with tempfile.TemporaryDirectory() as tmp, mlflow.start_run() as run:
        xgb_dir = os.path.join(tmp, _XGB_ARTIFACT)
        mlflow.xgboost.save_model(xgb_model, xgb_dir)
        mlflow.pyfunc.log_model(
            artifact_path=artifact_path,
            python_model=FraudScoringModel(config),
            artifacts={_XGB_ARTIFACT: xgb_dir},
            registered_model_name=registered_model_name,
        )
        return f"runs:/{run.info.run_id}/{artifact_path}"
