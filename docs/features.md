# The 15 Gold Features

> Source of truth for the FintelliGuard fraud model feature set.
> Any change here requires a simultaneous update to `ml/training/` and `ml/features/` (feature parity rule).

---

## Design principles

1. **Semantic, not raw.** Features are defined by *what they mean*, not which column they come from. Each data source (simulator stream / IEEE-CIS) has its own adapter that produces these 15.
2. **Computable in real time.** Each feature must be computable with a <5ms online Feature Store lookup. No features requiring full-table scans.
3. **Explainable.** Each feature must be explainable to a compliance analyst in one sentence. The Bedrock Agent uses feature importance for the verdict — so features must make sense to a human.
4. **Quality over quantity.** 15 good features, not 200. Each one justifies its existence.

---

## The feature parity challenge (read before writing code)

The two data sources have fundamentally different schemas:

| | Simulator stream | IEEE-CIS dataset |
|---|---|---|
| Amount | `amount` (clean float) | `TransactionAmt` |
| Card identity | `card_hash` | `card1`–`card6` (anonymized) |
| Velocity | computed from window | `C1`–`C14` (pre-computed counts) |
| Time deltas | computed from `timestamp` | `D1`–`D15` (pre-computed) |
| Device | `device_id` | `DeviceType`, `DeviceInfo`, `id_30`–`id_38` |
| Geography | `ip_country` | `addr1`, `addr2`, `dist1`, `dist2` |
| Anonymous | — | `V1`–`V339` (Vesta engineered) |

**The solution:** two adapters that produce the same 15 semantic features.

- `ml/features/adapter_stream.py` — computes the 15 from raw stream events via Spark window functions.
- `ml/features/adapter_ieee.py` — maps IEEE-CIS columns to the same 15.

**Honest caveat:** not all mappings are perfect 1:1. Where IEEE-CIS has no direct equivalent (e.g. `merchant_risk_score`), we use a proxy and document it in the "IEEE-CIS mapping" column below. This is acceptable — what is **not** acceptable is training on one feature set and serving another without knowing it.

---

## The 15 features

### Amount (3)

| # | Feature | Type | Definition | Stream computation | IEEE-CIS mapping |
|---|---|---|---|---|---|
| 1 | `amount_usd` | float | Transaction amount, normalized to USD | `amount` as-is | `TransactionAmt` (already USD) |
| 2 | `amount_log` | float | Log-transform for skew handling | `log1p(amount)` | `log1p(TransactionAmt)` |
| 3 | `amount_zscore` | float | Z-score vs card's historical mean | `(amount − card_mean) / card_std` (rolling 30d) | Proxy: z-score vs `card1` group mean |

### Velocity (4)

| # | Feature | Type | Definition | Stream computation | IEEE-CIS mapping |
|---|---|---|---|---|---|
| 4 | `txn_velocity_1h` | int | Card transactions in the last 1 hour | window count, 1h sliding | Proxy: `C1` (count feature) |
| 5 | `txn_velocity_24h` | int | Card transactions in the last 24h | window count, 24h sliding | Proxy: `C2` |
| 6 | `amount_sum_1h` | float | Sum of amounts in the last 1 hour | window sum, 1h sliding | Derived from `C1` × mean amount |
| 7 | `distinct_merchants_24h` | int | Distinct merchants in 24h | window approx_count_distinct | Proxy: `C4` |

### Identity & device (3)

| # | Feature | Type | Definition | Stream computation | IEEE-CIS mapping |
|---|---|---|---|---|---|
| 8 | `card_age_days` | int | Days since the card was first seen | `now − card_first_seen` (state store) | `D1` (days since first txn) |
| 9 | `device_seen_before` | bool | Has this device been used by the card before | state store lookup | Derived from `D` + `id_` cols |
| 10 | `device_txn_count_24h` | int | Transactions from this device in 24h | window count by device | Proxy: `C6` |

### Geography (2)

| # | Feature | Type | Definition | Stream computation | IEEE-CIS mapping |
|---|---|---|---|---|---|
| 11 | `country_mismatch` | bool | IP country differs from the card's usual | compare `ip_country` vs modal country | Derived: `addr2` vs modal `addr2` per card |
| 12 | `distinct_countries_24h` | int | Distinct countries for the card in 24h | window approx_count_distinct | Proxy: `dist1` bucketed |

### Merchant (2)

| # | Feature | Type | Definition | Stream computation | IEEE-CIS mapping |
|---|---|---|---|---|---|
| 13 | `merchant_risk_score` | float (0-1) | Historical fraud rate of the merchant | lookup in merchant risk table (Gold) | Proxy: fraud rate per `ProductCD` |
| 14 | `mcc_risk_tier` | int (1-5) | Risk tier of the merchant category | lookup in MCC risk map | Proxy: tier per `ProductCD` |

### Temporal (1)

| # | Feature | Type | Definition | Stream computation | IEEE-CIS mapping |
|---|---|---|---|---|---|
| 15 | `is_unusual_hour` | bool | Transaction at an unusual hour for the card | hour vs card's modal active hours | Derived from `TransactionDT` hour |

---

## State management (critical for the streaming path)

Features 3, 8, 9, 11, 15 require **per-card historical state** (e.g. historical mean amount, first seen, usual country). This does not exist in a raw stream event.

**Solution:** Spark Structured Streaming with stateful aggregation (`flatMapGroupsWithState`) + checkpointing. Per-card state is kept in a state store and updated on each event. Checkpointing ensures exactly-once and survival across restarts.

Features 4, 5, 6, 7, 10, 12 are **window aggregations** (sliding windows), computed with watermarking to handle late-arriving events.

---

## Feature Store schema

```
fintelliguard.features.txn_features (online + offline)
  ├── primary key: transaction_id
  ├── lookup key:  card_hash  (for online serving)
  ├── amount_usd, amount_log, amount_zscore           (float)
  ├── txn_velocity_1h, txn_velocity_24h               (int)
  ├── amount_sum_1h                                    (float)
  ├── distinct_merchants_24h                           (int)
  ├── card_age_days, device_txn_count_24h             (int)
  ├── device_seen_before, country_mismatch            (bool)
  ├── distinct_countries_24h                           (int)
  ├── merchant_risk_score                              (float)
  ├── mcc_risk_tier                                    (int)
  └── is_unusual_hour                                  (bool)
```

**Online store:** used by the Mosaic Model Serving endpoint at inference time — lookup by `card_hash`, <5ms.
**Offline store (Delta):** used by MLflow training — point-in-time correct joins to avoid data leakage.

---

## Validation gates (DLT expectations)

In Silver→Gold, each feature passes DLT expectations defined in `pipelines/gold/`:

- `amount_usd` > 0 and < 1,000,000 (outlier guard)
- `txn_velocity_1h` ≥ 0, `txn_velocity_24h` ≥ `txn_velocity_1h`
- `merchant_risk_score` ∈ [0, 1]
- `mcc_risk_tier` ∈ {1,2,3,4,5}
- No feature is null (impute or drop in Silver)

Rows that fail → quarantine table for inspection, not a silent drop.

---

## What we do NOT do

- **No** IEEE-CIS `V1`–`V339`. They are anonymized, non-explainable, and have no stream equivalent. Using them might raise AUC but would break feature parity and the explainability requirement.
- **No** features requiring a full-table scan at inference time.
- **No** target leakage — all window/state features are computed only from data *before* the current transaction.
