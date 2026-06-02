"""pyfunc round-trip: log to a local MLflow store, load, predict -> the contract."""

from __future__ import annotations

import mlflow
import pandas as pd

from ml.serving.endpoint import log_scoring_model
from ml.serving.scorer import CONTRACT_KEYS, ScoringConfig

from .conftest import SAMPLE_FEATURES


def test_pyfunc_logs_loads_and_predicts_contract(trained_xgb, tmp_path):
    mlflow.set_tracking_uri(f"file:{tmp_path}/mlruns")
    mlflow.set_experiment("fintelliguard-serving")

    model_uri = log_scoring_model(trained_xgb, config=ScoringConfig(model_version="fraud-xgb:7"))
    loaded = mlflow.pyfunc.load_model(model_uri)

    other = {**SAMPLE_FEATURES, "country_mismatch": False, "amount_zscore": -0.5}
    predictions = loaded.predict(pd.DataFrame([SAMPLE_FEATURES, other]))

    assert isinstance(predictions, list) and len(predictions) == 2
    for result in predictions:
        assert tuple(result.keys()) == CONTRACT_KEYS
        assert 0.0 <= result["fraud_score"] <= 1.0
        assert result["model_version"] == "fraud-xgb:7"
        assert result["decision_hint"] in {"allow", "review", "block"}
        assert result["top_features"]
        for item in result["top_features"]:
            assert set(item.keys()) == {"name", "value", "contribution"}
