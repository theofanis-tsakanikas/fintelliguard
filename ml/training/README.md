# ml/training/

XGBoost training with **MLflow** tracking on the IEEE-CIS fraud dataset.

Logs params, metrics, and the model to the MLflow registry. **Promotion gate:**
Staging→Production only when AUC-ROC ≥ 0.92 AND fraud-class precision ≥ 0.85 on the
held-out test set — record the metrics in the run before promoting.
