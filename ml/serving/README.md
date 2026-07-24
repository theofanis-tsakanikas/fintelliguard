# ml/serving/

Wraps the trained XGBoost model in the **`get_fraud_score()`** contract that Bedrock
calls (the single cross-cloud integration point — see `docs/bedrock-integration.md`).
Pure scoring is locally testable; **Mosaic AI Model Serving deployment + the online
Feature Store lookup are deferred to deploy**.

## Modules

- `scorer.py` — pure scoring wrapper. Input: a feature vector of **exactly** the
  canonical 14 features (`ml/features`), parity-checked (missing/extra → clear error).
  Output: the contract `{fraud_score, model_version, threshold, decision_hint,
  top_features[]}`.
  - `fraud_score` — model probability.
  - `threshold` / `decision_hint` — configurable bands: `< review` → **allow**,
    `review ≤ s < block` → **review**, `≥ block` → **block** (reported `threshold` is the
    review threshold, the Tier-2 trigger).
  - `top_features` — **per-prediction** TreeSHAP contributions (XGBoost `pred_contribs`,
    exact, no extra dependency): top-N by |contribution|, with canonical `name`, the
    feature's native `value`, and `contribution`. This is *why this transaction scored as
    it did*, not global importance.
  - `model_version` — injectable (the MLflow registry version in prod).
- `endpoint.py` — `FraudScoringModel`, an **MLflow pyfunc** wrapping the scorer: the
  deployable artifact for Mosaic AI Model Serving. Features are injected (a DataFrame),
  so it round-trips locally. `log_scoring_model` logs it (XGBoost artifact + scorer) under
  a configurable tracking URI.

## The contract (docs/bedrock-integration.md)

```json
{
  "fraud_score": 0.87,
  "model_version": "fraud-xgb:23",
  "threshold": 0.70,
  "decision_hint": "review",
  "top_features": [
    {"name": "txn_velocity_1h", "value": 9, "contribution": 0.31},
    {"name": "country_mismatch", "value": true, "contribution": 0.22}
  ]
}
```

## Local testing

Needs the OpenMP runtime for XGBoost (`brew install libomp` on macOS).

```bash
pip install -e ".[dev]"
pytest tests/serving      # contract fidelity, parity, bands, top_features, pyfunc round-trip
ruff check .
```

## Deferred to deploy

The Mosaic AI Model Serving endpoint (REST, autoscale, private VPC) and the online
Feature Store lookup by `card_hash` — the endpoint resolves the 14 features for a
transaction, then calls the scorer — run in the cloud and are deferred to deploy. This
layer ships the loadable, predict-able artifact and the scoring logic.
