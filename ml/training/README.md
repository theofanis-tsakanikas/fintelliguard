# ml/training/

XGBoost training on the **gold training features**, with **MLflow** tracking and the
documented promotion gate. Training logic is locally testable (real XGBoost + a local
MLflow file store); full-scale training on real IEEE-CIS + Databricks MLflow / Feature
Store is **deferred to deploy**.

## Modules

- `dataset.py` — feature access by **dependency injection**. `train_model` receives a
  features+label DataFrame, so tests pass a synthetic frame (`make_synthetic_frame`) and
  never touch the cloud. `prepare_xy` enforces parity: X is exactly the canonical 15
  features from `ml/features` (in order); the label (`is_fraud`) is never a feature.
  `load_gold_training_features` is the production loader (reads `gold.txn_features_training`).
- `train.py` — deterministic, seeded train/val/test split; trains XGBoost on the 15
  features; computes **AUC-ROC, PR-AUC, fraud-class precision/recall**; extracts
  **feature importance** (for the Bedrock verdict contract). Logs params/metrics/model/
  importance to MLflow under a **configurable tracking URI** (`TrainConfig.tracking_uri`:
  a local `file:` store for tests, a Databricks URI for real runs).
- `promote.py` — **pure** promotion gate. `evaluate_promotion(metrics)` returns
  promote/reject + reason per policy: **AUC-ROC ≥ 0.83 AND fraud-class precision ≥ 0.85**
  (Staging→Production). Fails closed on missing metrics.
- `registry.py` — MLflow Model Registry registration + stage transition, **guarded by
  `promote.py`**. Registry calls are cloud; dependencies are injectable so the wiring is
  unit-tested with mocks (`target_stage` is pure).

## Promotion policy

A model is promoted Staging→Production **only** when, on the held-out test set:

```
AUC-ROC >= 0.83   AND   fraud-class precision >= 0.85
```

Metrics are recorded in the MLflow run before any promotion.

## Local testing — needs the OpenMP runtime

XGBoost requires OpenMP. On macOS: `brew install libomp` (Linux wheels bundle it).

```bash
pip install -e ".[dev]"     # xgboost, scikit-learn, mlflow-skinny, ...
pytest tests/training
ruff check .
```

Tests train a real model on a small synthetic frame and log to a temp MLflow store; they
assert wiring/parity/determinism, **not** metric quality (synthetic data won't hit the bar).

## Deferred to deploy

Real IEEE-CIS training, the Databricks MLflow tracking server + Model Registry (stage
transitions need a database-backed registry), and Feature Store point-in-time joins run
in the cloud — deferred to the deploy phase.
