# infra/databricks/ — Terraform layer 2

Databricks **workspace** + **Unity Catalog** provisioning.

- Catalog: `fintelliguard` with `bronze`, `silver`, `gold` schemas (medallion).
- Secret scopes for credentials — never store secret values in `.tf`.
- Consumes AWS layer outputs (e.g. S3 buckets, VPC) via `terraform_remote_state`.
- Owns its own remote state in the bootstrap backend.
