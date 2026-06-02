# infra/databricks/ — Terraform layer 2 (workspace + Unity Catalog)

Provisions the Databricks **workspace** on a **customer-managed VPC** and the **Unity
Catalog** metastore + `fintelliguard.{bronze,silver,gold}` catalog/schemas. State lives
in the bootstrap S3 backend under key `infra/databricks/terraform.tfstate`
(eu-central-1).

> **Status: built + offline-validated only. Nothing is provisioned.** `plan`/`apply`
> need Databricks account auth and are **deferred to the deploy phase**.

## How it fits together

| Concern | Source |
|---|---|
| VPC, private subnets, SGs | **Consumed** from `infra/aws` via `terraform_remote_state` (private control, PrivateLink alignment) — never cross-layer state access |
| Cross-account IAM role, DBFS root bucket | Created here (the workspace's AWS prerequisites) using the Databricks-provided policy documents |
| Workspace (`mws_*`), metastore, assignment | **Account-level** Databricks provider (`databricks.account`) |
| Catalog + schemas | **Workspace-level** Databricks provider (`databricks.workspace`), host = the workspace this layer creates |

Two providers are installed: `databricks/databricks` and `hashicorp/aws`.

## Account auth — never hardcoded

Auth uses a Databricks **OAuth service principal** (account admin), supplied at run time
from your shell / Secrets Manager — never committed. The account provider authenticates
to the accounts console; the workspace provider reuses the same SP.

```bash
export TF_VAR_databricks_account_id="<account-uuid>"
export TF_VAR_databricks_client_id="<oauth-sp-client-id>"
export TF_VAR_databricks_client_secret="<oauth-sp-secret>"   # e.g. from Secrets Manager
# AWS creds for the aws provider come from ~/.aws (aws configure / SSO).
```

In CI these come from the secret store (GitHub Actions OIDC → Secrets Manager →
`TF_VAR_*`). The `databricks_client_secret` variable is marked `sensitive`.

## Networking note (data-plane SG)

This layer consumes the VPC + private subnets and the dedicated **Databricks data-plane
security group** from `infra/aws` (output `databricks_data_plane_sg_id`). That SG follows
the Databricks customer-managed VPC + back-end PrivateLink rules — intra-SG all-traffic
plus egress on 443 / 3306 / 6666 / 8443-8451. Override with `workspace_security_group_ids`
only if you need a different SG.

## Variables

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `aws_region` | string | `eu-central-1` | Region for the workspace + its AWS prerequisites. |
| `project` | string | `fintelliguard` | Prefix, catalog name, tag. |
| `environment` | string | `dev` | Env segment of names/tags. |
| `databricks_account_host` | string | `https://accounts.cloud.databricks.com` | Accounts console URL. |
| `databricks_account_id` | string | `""` | Account id; also the cross-account IAM external id. Supply via env. |
| `databricks_client_id` | string | `""` | OAuth SP client id. Supply via env / Secrets Manager. |
| `databricks_client_secret` | string (sensitive) | `""` | OAuth SP secret. Supply via env / Secrets Manager. |
| `workspace_name` | string | `""` | Workspace name; empty = `<project>-<environment>`. |
| `root_bucket_name` | string | `""` | DBFS root bucket; empty = `<project>-<environment>-dbfs-root-<account_id>`. |
| `workspace_security_group_ids` | list(string) | `[]` | Override data-plane SGs; empty = infra/aws dedicated Databricks data-plane SG. |
| `pricing_tier` | string | `PREMIUM` | Required for Unity Catalog. |
| `metastore_name` | string | `""` | Metastore name; empty = `<project>-<aws_region>`. |
| `metastore_storage_root` | string | `""` | Optional metastore-level S3 storage root; empty = omit. |
| `schemas` | list(string) | `["bronze","silver","gold"]` | Medallion schemas in the catalog. |

## Outputs

`workspace_id`, `workspace_url`, `metastore_id`, `catalog_name`, `schema_names`,
`cross_account_role_arn`, `root_bucket_name`, `network_id`.

## Validate / deploy

```bash
cd infra/databricks

# Offline (no account auth needed) — what this phase runs:
terraform init -backend=false   # installs databricks + aws providers
terraform fmt -check -recursive
terraform validate

# Deploy phase (later): real backend + account auth, then review the plan.
terraform init
terraform plan
terraform apply
```
