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
from mlflow.models import infer_signature

from ml.features.schema import FEATURE_SPECS
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


def _example_value(dtype: type) -> object:
    """A single valid value at a feature's canonical dtype, for the signature example."""
    if dtype is bool:
        return False
    if dtype is int:
        return 0
    return 0.0


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

    # Unity Catalog REFUSES to register a model with no signature:
    #
    #     MlflowException: All models in the Unity Catalog must be logged with a model
    #     signature containing both input and output type specifications.
    #
    # The signature is inferred from a real scoring pass rather than hand-declared, so it
    # cannot drift from what the pyfunc actually returns (the 14-feature input frame and the
    # get_fraud_score contract, top_features included).
    #
    # The example row uses each feature's CANONICAL dtype, not all-double: the schema in
    # docs/features.md and FEATURE_SPECS declares a mix of float / int / bool (e.g.
    # `country_mismatch` is bool), and MLflow does not auto-cast bool to double — an
    # all-double signature would reject the very inputs serving sends. The example never
    # leaves this function.
    example_row = {spec.name: _example_value(spec.dtype) for spec in FEATURE_SPECS}
    example_input = pd.DataFrame([example_row])

    # `top_features[].value` echoes each feature's own value, whose canonical dtype varies
    # (float, int, bool) across the list. infer_signature tries to MERGE the array elements
    # into one object schema and fails on the heterogeneity ("value: long" vs "value: double").
    # The displayed value is coerced to float for the SIGNATURE example only — the schema
    # needs a homogeneous array; the real prediction still returns each value at its own type.
    scored = FraudScorer(xgb_model, config).score(example_row)
    example_output = [
        {
            **scored,
            "top_features": [{**f, "value": float(f["value"])} for f in scored["top_features"]],
        }
    ]
    signature = infer_signature(example_input, example_output)

    with tempfile.TemporaryDirectory() as tmp, mlflow.start_run() as run:
        xgb_dir = os.path.join(tmp, _XGB_ARTIFACT)
        mlflow.xgboost.save_model(xgb_model, xgb_dir)
        mlflow.pyfunc.log_model(
            artifact_path=artifact_path,
            python_model=FraudScoringModel(config),
            artifacts={_XGB_ARTIFACT: xgb_dir},
            registered_model_name=registered_model_name,
            signature=signature,
            input_example=example_input,
        )
        return f"runs:/{run.info.run_id}/{artifact_path}"
