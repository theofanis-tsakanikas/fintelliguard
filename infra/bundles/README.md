# infra/bundles/ — Databricks Asset Bundles

Deployable Databricks workloads as **DABs** (`databricks.yml`):

- DLT pipelines for the bronze / silver / gold medallion layers (`pipelines/`).
- Mosaic AI **Model Serving** endpoint for the XGBoost fraud scorer (`ml/serving/`).

Bundles are the deploy mechanism for code in `pipelines/` and `ml/`; cluster configs
set **auto-terminate after 30 min idle**. Validate with `databricks bundle validate`
and deploy per target environment.
