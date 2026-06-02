# infra/bundles/ — Databricks Asset Bundles

One project bundle (`databricks.yml` + `resources/*.yml`) that deploys **all** Databricks
workloads. **Build + offline-validate only — `databricks bundle deploy` is deferred.**

## What it deploys

| Resource file | Resource(s) | Notes |
|---|---|---|
| `resources/pipelines.yml` | DLT `medallion` pipeline | Runs the `@dlt.*` modules from `pipelines/` into `fintelliguard.{bronze,silver,gold}` (docs/data-flow.md). `continuous` is variable (streaming vs triggered). |
| `resources/model_serving.yml` | `fraud_score` serving endpoint | The `ml/serving` XGBoost `get_fraud_score` endpoint Bedrock + copilot call. Scale-to-zero autoscale. |
| `resources/agent_serving.yml` | `copilot_agent` serving endpoint | The Mosaic AI Agent Framework agent (`agents/databricks`) served for analysts. |
| `resources/vector_search.yml` | Vector Search endpoint + DELTA_SYNC index | Over the resolved-cases table, embedding `case_text` (docs/copilot-design.md); feeds `search_similar_cases`. |
| `resources/governance.yml` | `ml` + `features` schemas, registered models, secret scope | Least-privilege **UC grants** (USE_SCHEMA / SELECT / EXECUTE to the analyst group); serving endpoints grant `CAN_QUERY`. |
| `scripts/genie_space.py` | Genie space (fallback) | See below. |

## Genie — fallback (DABs coverage gap)

**Verified:** the DABs schema (CLI v1.1.0) covers `pipelines`, `model_serving_endpoints`,
`vector_search_endpoints`, `vector_search_indexes`, `registered_models`, `schemas`, and
`secret_scopes` — but there is **no `genie_space` resource type**. So the Genie space
(backing `query_lakehouse`) is created via a thin documented fallback,
`scripts/genie_space.py` (Databricks SDK / Genie spaces API), run manually at deploy time.

## Secrets & cross-layer inputs

- **Secrets** live in the `fintelliguard` Databricks **secret scope**; values are injected
  out of band and read as `{{secrets/fintelliguard/<key>}}`. Never hardcoded in the bundle.
- **infra/databricks outputs** feed the deploy as variables: `catalog` (catalog_name), the
  workspace `host` (workspace_url — set via `DATABRICKS_HOST`/profile, since auth fields
  can't use bundle variables), `warehouse_id`, plus model/endpoint names.
- infra/databricks owns the catalog + bronze/silver/gold schemas; SELECT grants on those
  (for query_lakehouse/Genie) are managed in that UC layer. This bundle owns the `ml` /
  `features` schemas, model containers, and endpoint permissions.

## Validate (offline)

```bash
cd infra/bundles
databricks bundle validate -t dev
```

This performs full **schema/config validation** offline (unknown fields, type errors are
reported). It then attempts workspace auth for the remaining checks — that step, and
`databricks bundle deploy`, require workspace credentials and are **deferred to deploy**.
A clean run shows no schema warnings, only the expected auth error.

## Deferred to deploy

`databricks bundle deploy`, the Genie space creation, registering the model/agent versions
(`fraud_model_version` / `agent_model_version`), populating the resolved-cases table +
vector index sync, and packaging the `@dlt` modules as a wheel (they use relative imports)
— all run against a live workspace.
