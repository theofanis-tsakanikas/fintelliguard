# Data Flow

Two orthogonal paths feed FintelliGuard. They are not parallel computations of the
same thing — one feeds model **training**, the other feeds real-time **inference**.
They converge only at the Model Serving endpoint.

## Streaming path (real-time inference)

```
Python simulator (~500 txns/sec)
  → Kafka / MSK            topic: txn.raw, 3 partitions
  → Spark Structured Streaming
  → bronze.transactions_stream    raw JSON, _rescued_data, ingest metadata
  → silver.transactions_clean     validated, typed, no nulls, enriched (mcc_risk, ISO country)
  → gold.txn_features_realtime     the 15 features (velocity, z-score, risk flags)
  → Mosaic AI Feature Store (online)   <5ms lookup by card_hash
```

State features (z-score, card_age, device_seen, country_mismatch, unusual_hour) use
Spark `flatMapGroupsWithState` + checkpointing. Window features (velocities, distinct
counts) use sliding windows with watermarking for late events.

## Batch path (model training)

```
IEEE-CIS dataset (590k labeled txns)
  → S3 raw landing        s3://.../raw/ieee-cis/
  → Auto Loader           schema rescue, row_hash
  → bronze.ieee_cis_raw
  → silver.ieee_cis_clean   imputed (median V-cols), encoded, typed
  → gold.txn_features_training   same 15 features + isFraud label, 70/15/15 split
  → MLflow training (XGBoost)
```

## Convergence and consumption

```
                         Mosaic AI Model Serving (XGBoost)
                                    ▲
              online features ──────┘
                                    │ get_fraud_score()
        ┌───────────────────────────┼───────────────────────────┐
        │ Tier 1: every txn         │ Tier 2: Bedrock (suspicious)│
        │ Tier 3: copilot (flagged, human, async)                │
        └─────────────────────────────────────────────────────────┘
```

- **Tier 1** consumes the score directly in the transaction path.
- **Tier 2** (Bedrock) calls `get_fraud_score()` across the cloud boundary, then RAG + Guardrails.
- **Tier 3** (copilot) reads the lakehouse (Genie NL→SQL), Vector Search over case
  embeddings, and `get_fraud_score()` for context.

## Feature parity

Both paths must produce identical feature semantics. Two adapters enforce this:
`ml/features/adapter_stream.py` and `ml/features/adapter_ieee.py`. See `features.md`
for the per-feature source mapping and the honest proxy caveats.
