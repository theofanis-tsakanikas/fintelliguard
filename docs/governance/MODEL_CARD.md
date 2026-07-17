# Model Card — FintelliGuard Fraud Scorer

> Generated from code by `python -m ml.governance.generate`. Do not edit by hand.

## Overview

- **Model:** XGBoost binary classifier (`binary:logistic`).
- **Task:** real-time fraud probability for a single card transaction (Tier 1).
- **Output contract:** `fraud_score`, `model_version`, `threshold`, `decision_hint`, `top_features` (per-prediction TreeSHAP contributions).
- **Role in the system:** scores 100% of transactions; the ~1% above the review threshold are escalated to the Tier-2 Bedrock compliance agent.

## Inputs — the canonical 15 features

Both adapters (stream + IEEE-CIS) must produce exactly this schema; parity is enforced by test. Ranges are the DLT validation gates.

| Feature | Type | Valid range |
| --- | --- | --- |
| `amount_usd` | float | > 0 < 1e+06 |
| `amount_log` | float | >= 0 |
| `amount_zscore` | float | — |
| `txn_velocity_1h` | int | >= 0 |
| `txn_velocity_24h` | int | >= 0 |
| `amount_sum_1h` | float | >= 0 |
| `distinct_merchants_24h` | int | >= 0 |
| `card_age_days` | int | >= 0 |
| `device_seen_before` | bool | — |
| `device_txn_count_24h` | int | >= 0 |
| `country_mismatch` | bool | — |
| `distinct_countries_24h` | int | >= 0 |
| `mcc_risk_tier` | int | in [1, 2, 3, 4, 5] |
| `is_unusual_hour` | bool | — |

## Decision bands

- **review threshold:** 0.7 (Tier-2 trigger)
- **block threshold:** 0.9
- Scores in `[0.7, 0.9)` → `review`; `>= 0.9` → `block`; else `allow`.

## Promotion gate (Staging → Production)

Promotion requires **AUC-ROC ≥ 0.92** AND **fraud-class precision ≥ 0.85** on the held-out test set. Missing metrics fail closed.

## Explainability

Every score ships `top_features`: exact per-prediction TreeSHAP contributions (`pred_contribs`), i.e. *why this transaction* scored as it did — not global importance. These drive the Tier-2 agent's reasoning and the verdict faithfulness check.

## Limitations & known risks

- Trained on synthetic + IEEE-CIS data; absolute rates are illustrative, not production-measured.
- A fraud label is never a feature; window features use only strictly-prior transactions (no target leakage — proven by test).
- Distribution shift degrades the score silently — monitored by the drift detector (`ml/monitoring/drift.py`), PSI bands below.

## Monitoring

- **Drift:** PSI per feature — stable `< 0.1`, watch `[0.1, 0.25)`, alert `≥ 0.25` (+ two-sample KS).
- **Output:** every Tier-2 verdict passes the verdict acceptance gate before reaching an analyst.

