# pipelines/

Databricks **DLT** tables, organized by medallion layer. Naming convention:
`bronze.<name>`, `silver.<name>`, `gold.<name>` in Unity Catalog `fintelliguard`.

```
sources → bronze (raw, append) → silver (clean, conformed) → gold (15 features)
```

- `bronze/` — raw ingestion (Kafka/MSK stream + S3 Auto Loader batch).
- `silver/` — cleaned, deduplicated, conformed transactions.
- `gold/` — the **15 fraud features** that train the model and serve at inference.

See `@docs/data-flow.md` and `@docs/features.md`.
