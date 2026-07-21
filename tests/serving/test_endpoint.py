"""pyfunc round-trip: log to a local MLflow store, load, predict -> the contract."""

from __future__ import annotations

import pathlib

import mlflow
import mlflow.artifacts
import pandas as pd

from ml.serving.endpoint import log_scoring_model
from ml.serving.scorer import CONTRACT_KEYS, ScoringConfig

from .conftest import SAMPLE_FEATURES


def test_pyfunc_logs_loads_and_predicts_contract(trained_xgb, tmp_path):
    mlflow.set_tracking_uri(f"file:{tmp_path}/mlruns")
    mlflow.set_experiment("fintelliguard-serving")

    model_uri = log_scoring_model(trained_xgb, config=ScoringConfig(model_version="fraud-xgb:7"))
    loaded = mlflow.pyfunc.load_model(model_uri)

    # Unity Catalog registration REQUIRES a signature with BOTH input and output specs, or it
    # refuses the model after it has already been trained (deploy run 29799949900). The input
    # must carry the canonical mixed dtypes — all-double would reject the bool features that
    # serving sends.
    signature = loaded.metadata.signature
    assert signature is not None and signature.inputs is not None, "no input signature — UC rejects"
    assert signature.outputs is not None, "no output signature — UC rejects"
    input_types = {c.name: c.type.name for c in signature.inputs.inputs}
    assert input_types["country_mismatch"] == "boolean", (
        "a bool feature is typed non-bool in the signature — serving input would be rejected"
    )

    # The `ml` package must travel INSIDE the model. FraudScoringModel is pickled with a
    # reference to `ml.serving.endpoint` and imports `ml.serving.scorer` -> `ml.features.*`;
    # the serving container has none of that on its path, so without code_paths the model
    # fails to load and the endpoint reaches UPDATE_FAILED (deploy run 29808739561). Check the
    # artifact carries the code, not just that the round-trip worked in-process (where `ml` is
    # already importable and would mask the omission).
    local = mlflow.artifacts.download_artifacts(model_uri)
    bundled = pathlib.Path(local) / "code" / "ml" / "serving" / "endpoint.py"
    assert bundled.is_file(), (
        "the model artifact does not bundle the `ml` package (code_paths) — the serving "
        "container cannot import FraudScoringModel and the endpoint fails to start"
    )

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
