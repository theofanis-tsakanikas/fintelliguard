# infra/

Infrastructure as Code. **Every cloud resource lives here** — no console deployments, ever.

Three Terraform layers, each owning its **own remote state**. Layers never reference
each other's state directly; they communicate through outputs + data sources.

| Layer | Path | Owns |
|---|---|---|
| 1 — AWS | `aws/` | MSK, S3, Secrets Manager, API Gateway, IAM, KMS |
| 2 — Databricks | `databricks/` | Workspace + Unity Catalog |
| 3 — Bundles | `bundles/` | Databricks Asset Bundles: DLT pipelines + Model Serving |

The remote-state backend (S3 bucket + DynamoDB lock table) is created **once** by
`aws/bootstrap/` using local state. See `aws/bootstrap/README.md`.

> Always `terraform plan` before `apply`. `terraform destroy` per layer when idle.
