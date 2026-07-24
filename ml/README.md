# ml/

The fast scorer (**Tier 1**): XGBoost over the 14 Gold features, <50 ms per transaction.

- `training/` — MLflow + XGBoost training on IEEE-CIS.
- `features/` — Feature Store definitions + adapters (`adapter_stream.py`, `adapter_ieee.py`).
- `serving/` — Mosaic AI Model Serving endpoint config (REST, autoscale).

**Feature parity:** the same 14 Gold features train and serve. **Promotion policy:**
Staging→Production only when AUC-ROC ≥ 0.83 AND fraud-class precision ≥ 0.85 on held-out
test, with metrics documented in the MLflow run.
