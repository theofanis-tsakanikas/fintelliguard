# ml/features/

The **feature-parity contract**: one canonical 15-feature schema, produced by two
adapters so training (IEEE-CIS) and serving (stream) can never diverge. Pure Python,
fully unit-tested locally — no Spark, no cloud. See `@docs/features.md`.

## Modules

- `schema.py` — **canonical** 15-feature schema: names, types, valid ranges (the DLT
  gates), plus `FeatureVector`/`FeatureRecord` and `validate_feature_vector`. Single
  source of truth; both adapters must satisfy it.
- `transforms.py` — shared **pure** functions for source-independent features
  (`amount_log`, `zscore`, risk tier/score lookups, `is_unusual_hour`, mismatch). Both
  adapters call these, so they cannot drift.
- `adapter_stream.py` — the 15 features from the simulator's bronze contract. Window/
  state features (velocities, distinct counts, card age, device-seen, country mismatch,
  unusual hour) are **pure functions over a per-card history list** — no Spark. The
  Spark Structured Streaming wiring is deferred to `pipelines/gold`, which just calls
  these. **No target leakage**: only transactions strictly *before* the current one are
  used (history is defensively time-filtered).
- `adapter_ieee.py` — maps IEEE-CIS columns to the same 15 via the documented proxies
  (C1/C2/C4/C6, D1, addr2, dist1, ProductCD, TransactionDT). Each proxy is noted inline;
  per-card aggregates arrive via `CardContext`.
- `feature_store.py` — the Feature Store **definition** for
  `fintelliguard.features.txn_features` (PK `transaction_id`, lookup `card_hash`,
  online+offline). Spec only — registration is deferred to the cloud/bundles layer.

## Parity, by construction

The canonical schema + shared transforms are the enforcement mechanism: tests assert
**both** adapters emit exactly `FEATURE_NAMES` with matching types and in-range values,
and that simulator-injected fraud produces the expected feature signatures.

```bash
pytest tests/features
ruff check .
```
