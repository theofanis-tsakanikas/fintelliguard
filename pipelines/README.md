# pipelines/

Databricks **DLT** medallion pipelines. Each layer separates **pure, locally-testable
Spark transforms** (`*_transforms.py`) from a **thin DLT framework layer**
(`*_pipeline.py`) that only runs on Databricks. Naming: `bronze.<name>`, `silver.<name>`,
`gold.<name>` in Unity Catalog `fintelliguard`.

```
sources → bronze (raw, rescued) → silver (clean, validated, enriched) → gold (14 features)
```

## Layers

| Layer | Transforms | DLT tables |
|---|---|---|
| **bronze** | `parse_transactions_stream` (Kafka JSON → contract, schema rescue, ingest metadata), `parse_ieee_raw` (Auto Loader shape, `row_hash`) | `bronze.transactions_stream`, `bronze.ieee_cis_raw` |
| **silver** | `cleanse_transactions` (type/normalize/enrich, ISO country, `mcc_risk_tier`), `cleanse_ieee` (type/impute) | `silver.transactions_clean` + `…_quarantine`, `silver.ieee_cis_clean` + `…_quarantine` |
| **gold** | `build_realtime_features` / `build_training_features` — **wire the tested `ml/features` adapters** into Spark via `applyInPandas` (no reimplementation) | `gold.txn_features_realtime` + `…_quarantine`, `gold.txn_features_training` |

**Gold reuses, never reimplements.** `build_realtime_features` replays each card's
history (in timestamp order, strictly-before only) through `adapter_stream.compute_features`;
`build_training_features` maps each IEEE row through `adapter_ieee.map_row` with per-card
aggregates. The feature logic and its tests live in `ml/features/`.

**Expectations + quarantine.** Gates in `*_transforms.py` (and `gold_transforms.GOLD_GATES`)
match the `docs/features.md` validation gates (amount range, velocity monotonicity,
`merchant_risk ∈ [0,1]`, `mcc_risk_tier ∈ {1..5}`, no nulls). The same gates drive both
the `@dlt.expect_all` metrics and the quarantine split — failing rows are **routed to a
`*_quarantine` table, never silently dropped**.

## Local testing — needs a JDK

The transforms are tested for real in **local PySpark** (`tests/pipelines/`), so a Java
runtime is required:

- **Java 8, 11, or 17** (a JDK). macOS: `brew install openjdk@17`.
- The test fixture (`tests/pipelines/conftest.py`) best-effort discovers `JAVA_HOME`
  (via `/usr/libexec/java_home` or common Homebrew/Linux paths) and pins PySpark's worker
  interpreter to the project venv. If `JAVA_HOME` is already exported it is respected.
- Dev deps: `pip install -e ".[dev]"` (pyspark, pandas, pyarrow, ruff, pytest).

```bash
pytest tests/pipelines     # real local-Spark transform tests
ruff check .
```

## Deferred to the deploy phase

The `*_pipeline.py` modules (the `@dlt.*` decorator layer) are **import/lint-validated
only** here — a test stubs `dlt` and asserts the table functions register. Full **DLT +
Structured Streaming execution** — checkpointing, watermarking, stateful streaming
(designed as `flatMapGroupsWithState`; today a full-table recompute — see docs/features.md),
Auto Loader, Kafka — runs only on Databricks and is deferred
to the deploy phase.
