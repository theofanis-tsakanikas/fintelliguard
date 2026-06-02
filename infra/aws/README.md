# infra/aws/ — Terraform layer 1 (AWS edge)

The AWS-side foundation: **VPC**, **KMS**, **S3** raw landing, **Secrets Manager**
placeholders, least-privilege **IAM**, and a cost-guarded **MSK** cluster. State lives
in the bootstrap S3 backend under key `infra/aws/terraform.tfstate`. Everything here is
consumed by later layers / the Bedrock Lambda via `terraform_remote_state` + outputs —
never by cross-layer state access.

## What this layer creates

| Group | Resources |
|---|---|
| Networking | VPC, public + private subnets ×`az_count`, IGW, single NAT + EIP, public/private route tables, interface VPC endpoints (Secrets Manager, KMS; Databricks PrivateLink optional), 3 security groups |
| KMS | One customer-managed key (rotation on) + alias; balanced key policy (root admin · `ViaService` use · grants for AWS resources) |
| S3 | Raw landing bucket — versioned, KMS-encrypted, all public access blocked, lifecycle expiry |
| Secrets | Empty, KMS-encrypted secret placeholders (Databricks token, LangSmith key) |
| IAM | Lambda action-group role (Mosaic) · MSK IAM-auth access role — both least-privilege |
| MSK | Cluster **guarded by `enable_msk`, off by default** |

## `enable_msk` — the cost guard

MSK runs brokers 24/7 and is the most expensive resource in this layer. Per CLAUDE.md,
**dev uses local Kafka (Docker)**; MSK is provisioned **only for integration testing and
the final demo**.

- `enable_msk = false` (default) → `terraform plan/apply` does **not** create the
  cluster, the SG and IAM role still exist (stable identities/outputs), and the MSK
  outputs return `null`.
- Set `enable_msk = true` only for an integration run, then set it back to `false`
  (or `terraform destroy`) when idle.

```bash
terraform plan                       # MSK absent
terraform plan -var enable_msk=true  # MSK present (integration only)
```

## Variables

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `aws_region` | string | `eu-central-1` | Region for this layer's resources. Independent of the state backend region (see `backend.tf`). |
| `project` | string | `fintelliguard` | Name prefix + tag. |
| `environment` | string | `dev` | Env segment of names/tags (`dev`/`stg`/`prod`). |
| `tags` | map(string) | `{}` | Extra tags merged over provider `default_tags`. |
| `vpc_cidr` | string | `10.20.0.0/16` | VPC CIDR. |
| `az_count` | number | `2` | AZs to span; also the MSK broker count (min 2). |
| `public_subnet_cidrs` | list(string) | `["10.20.0.0/24","10.20.1.0/24"]` | Public subnet CIDRs, one per AZ. |
| `private_subnet_cidrs` | list(string) | `["10.20.16.0/20","10.20.32.0/20"]` | Private subnet CIDRs, one per AZ. |
| `databricks_privatelink_service_name` | string | `""` | Databricks PrivateLink service name for private Mosaic reach; empty = endpoint not created yet. |
| `raw_bucket_name` | string | `""` | Override raw bucket name; empty = `<project>-raw-<account_id>`. |
| `raw_retention_days` | number | `90` | Lifecycle expiry for raw objects (cost cap). |
| `raw_noncurrent_retention_days` | number | `30` | Expiry for overwritten (noncurrent) versions. |
| `managed_secret_names` | list(string) | `["databricks/token","langsmith/api-key"]` | Secret placeholders to create (paths prefixed `<project>/<environment>/`). |
| `secret_recovery_window_days` | number | `7` | Recovery window for deleted secrets. |
| `enable_msk` | bool | `false` | **Cost guard.** Provision MSK only when true. |
| `msk_kafka_version` | string | `3.6.0` | Kafka version. |
| `msk_broker_instance_type` | string | `kafka.t3.small` | Smallest viable broker type. |
| `msk_broker_ebs_gb` | number | `10` | EBS GB per broker. |

## Outputs

`vpc_id`, `vpc_cidr`, `public_subnet_ids`, `private_subnet_ids`,
`lambda_security_group_id`, `msk_security_group_id`, `endpoints_security_group_id`,
`kms_key_arn`, `kms_key_alias`, `raw_bucket_name`, `raw_bucket_arn`, `secret_arns`,
`lambda_role_arn`, `msk_access_role_arn`, `msk_cluster_arn` (null when disabled),
`msk_bootstrap_brokers_sasl_iam` (null when disabled), `region`.

## Secrets — values are injected at runtime, never via Terraform

Terraform creates the secret **containers** only; it never writes a value (no
`aws_secretsmanager_secret_version`), so no secret ever lands in state or git. After
apply, inject values out of band:

```bash
aws secretsmanager put-secret-value \
  --secret-id fintelliguard/dev/databricks/token \
  --secret-string "$DATABRICKS_TOKEN"
```

…or from CI (GitHub Actions via OIDC → `put-secret-value`). Workloads fetch them at
runtime with `GetSecretValue`; the Lambda role's read is scoped to these secret ARNs and
`kms:Decrypt` is conditioned on `kms:ViaService = secretsmanager`.

## IAM — least-privilege, no wildcards

Authored inline policies are scoped to explicit ARNs — no `Action:*`, no `Resource:*`,
no hardcoded account ids (all ARNs come from `aws_caller_identity` / `aws_partition`).
The Lambda additionally attaches the two AWS-**managed** execution policies
(`AWSLambdaBasicExecutionRole`, `AWSLambdaVPCAccessExecutionRole`); their ENI actions
use `Resource:*` because the network interface does not pre-exist — this is the standard,
audited grant for a VPC Lambda, not an authored wildcard.

## Usage

```bash
cd infra/aws
terraform init        # needs AWS creds + an applied bootstrap backend
terraform plan        # review — MSK must be ABSENT with the default enable_msk=false
terraform apply       # only after reviewing the plan

# Offline checks (no AWS account needed):
terraform init -backend=false
terraform validate
terraform fmt -check -recursive
```

> Prerequisites (Terraform via HashiCorp tap; `.venv` tooling) and the one-time backend
> bootstrap are documented in `infra/aws/bootstrap/README.md`.
