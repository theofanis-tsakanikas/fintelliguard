"""End-to-end training on a small synthetic set + determinism (real XGBoost + MLflow).

Metric QUALITY is not asserted — synthetic data won't reach the promotion thresholds.
"""

from __future__ import annotations

from ml.features.schema import FEATURE_NAMES
from ml.training.dataset import make_synthetic_frame, prepare_xy
from ml.training.train import TrainConfig, split_dataset, train_model

# Small, fast, deterministic XGBoost params for tests.
_FAST_XGB = {
    "n_estimators": 60,
    "max_depth": 4,
    "learning_rate": 0.2,
    "objective": "binary:logistic",
    "eval_metric": "auc",
    "tree_method": "hist",
    "n_jobs": 1,
}


def _config(tmp_path, seed=5):
    return TrainConfig(
        seed=seed, tracking_uri=f"file:{tmp_path}/mlruns", xgb_params=dict(_FAST_XGB)
    )


def test_train_end_to_end_logs_and_returns(tmp_path):
    frame = make_synthetic_frame(n_rows=600, seed=5)
    result = train_model(frame, _config(tmp_path))

    assert result.run_id
    assert set(result.metrics) == {"auc_roc", "pr_auc", "fraud_precision", "fraud_recall"}
    assert all(0.0 <= v <= 1.0 for v in result.metrics.values())
    # Importance covers exactly the canonical features.
    assert set(result.feature_importance) == set(FEATURE_NAMES)
    # The model saw exactly the canonical features — no label leakage.
    assert result.model.n_features_in_ == len(FEATURE_NAMES)
    assert (tmp_path / "mlruns").exists()  # logged to the local MLflow store


def test_training_is_deterministic(tmp_path):
    frame = make_synthetic_frame(n_rows=600, seed=9)
    config = _config(tmp_path, seed=9)
    first = train_model(frame, config)
    second = train_model(frame, config)
    assert first.metrics == second.metrics
    assert first.feature_importance == second.feature_importance


def test_split_is_deterministic_and_partitions_all_rows(tmp_path):
    frame = make_synthetic_frame(n_rows=400, seed=2)
    x, y = prepare_xy(frame)
    config = _config(tmp_path, seed=2)

    a = split_dataset(x, y, config)
    b = split_dataset(x, y, config)
    assert list(a.x_test.index) == list(b.x_test.index)
    assert list(a.x_train.index) == list(b.x_train.index)

    covered = set(a.x_train.index) | set(a.x_val.index) | set(a.x_test.index)
    assert len(covered) == len(x)  # disjoint and complete
