# Deploy runbook

The ordered, one-time sequence to provision FintelliGuard to real cloud accounts. **This
is deferred** in the repo — everything here is built + offline-validated; running it
incurs cost. Region throughout: **eu-central-1**.

> Each step is gated. Always `terraform plan` before `apply`, and review it. The CI
> `ci.yml` workflow reproduces all the *local* gates with no cloud creds; the
> `bootstrap` / `deploy` / `destroy` workflows automate this runbook behind manual,
> secrets-gated dispatch.

## Prerequisites

**Accounts & auth**
- **AWS account** with credentials able to create VPC, KMS, S3, MSK, IAM, Secrets
  Manager, Bedrock, OpenSearch Serverless, Lambda. `aws sts get-caller-identity` works.
- **Databricks account on AWS** with **account-level auth** — an OAuth **service
  principal** that is an *account admin* (needed to create the workspace + Unity Catalog
  metastore). You will export:
  - `TF_VAR_databricks_account_id`, `TF_VAR_databricks_client_id`,
    `TF_VAR_databricks_client_secret`
  - and, after the workspace exists, `DATABRICKS_HOST` + `DATABRICKS_TOKEN` (workspace).

**Tools**
- Terraform ≥ 1.5 via the HashiCorp tap (`brew tap hashicorp/tap && brew install
  hashicorp/tap/terraform` — the plain formula was removed after the BSL change).
- Databricks CLI (`brew install databricks`), AWS CLI, **Java 17**, **Python 3.11**,
  OpenMP (`brew install libomp`).
- `pip install -e ".[dev]"`, then `pytest` — confirm green before touching the cloud.

## Resolve-at-deploy (decide these first)

- **Bedrock model region availability.** Verify Claude (Haiku) is available and
  **model access is granted** in **eu-central-1** before step 4
  (`aws bedrock list-foundation-models --region eu-central-1`). If absent, request access
  or set `agents/bedrock/terraform` `var.foundation_model` / region accordingly.
- **Databricks account-level auth** (above) is required for steps 3 and the metastore.
- **`enable_msk`** stays `false` (dev uses local Kafka). Set `-var enable_msk=true` only
  for an integration run, then turn it back off — MSK runs brokers 24/7.
- **Private connectivity.** Set `infra/aws` `var.databricks_privatelink_service_name`
  (once the Databricks PrivateLink service is known) so Bedrock reaches Mosaic privately.

## Ordered sequence

### 1. State backend (bootstrap)
Creates the versioned, encrypted S3 state bucket + DynamoDB lock table. Run once.
```bash
cd infra/aws/bootstrap
terraform init && terraform plan && terraform apply
```

### 2. infra/aws — edge foundation
VPC (2 AZ, single NAT), KMS CMK, raw S3 bucket, Secrets placeholders, least-privilege
IAM, the Databricks data-plane SG, interface endpoints. MSK stays off.
```bash
cd infra/aws
terraform init && terraform plan && terraform apply
# Inject secret values out of band (never in TF):
aws secretsmanager put-secret-value --secret-id fintelliguard/dev/databricks/token \
  --secret-string "$DATABRICKS_TOKEN"
```

### 3. infra/databricks — workspace + Unity Catalog
Customer-managed VPC workspace (consumes infra/aws via remote state) + metastore +
`fintelliguard.{bronze,silver,gold}` schemas. Needs account-level auth exported.
```bash
export TF_VAR_databricks_account_id=... TF_VAR_databricks_client_id=... TF_VAR_databricks_client_secret=...
cd infra/databricks
terraform init && terraform plan && terraform apply
# Note the workspace_url output -> export DATABRICKS_HOST / create a DATABRICKS_TOKEN.
```

### 4. agents/bedrock/terraform — Tier-2 zone
**First verify Bedrock Claude availability in eu-central-1** (see resolve-at-deploy).
Then the agent + Knowledge Base (OpenSearch Serverless) + Guardrail + FraudScoring
action-group Lambda (consumes infra/aws role/secrets/KMS/subnets).
```bash
cd agents/bedrock/terraform
terraform init && terraform plan && terraform apply
```
> The Lambda's `MOSAIC_ENDPOINT_URL` is empty until the serving endpoint exists (step 6).
> After step 6, set `-var mosaic_endpoint_url=<serving-url>` and re-apply.

### 5. infra/bundles — Databricks Asset Bundles
DLT pipeline, model-serving + agent endpoints, Vector Search, UC grants, secret scope.
```bash
export DATABRICKS_HOST=... DATABRICKS_TOKEN=...
cd infra/bundles
databricks bundle validate -t dev
databricks bundle deploy  -t dev
# Genie space (no DABs resource yet) — documented SDK fallback:
python scripts/genie_space.py --warehouse-id <sql-warehouse-id> --catalog fintelliguard
```

### 6. Data → model → serving
```bash
# Load the training data and run the batch DLT path:
aws s3 cp ieee-cis/ s3://fintelliguard-raw/raw/ieee-cis/ --recursive
# In Databricks: run the DLT pipeline to build gold.txn_features_training, then:
#   ml/training  -> train_model (TrackConfig.tracking_uri = databricks), log metrics
#   promotion gate: AUC-ROC >= 0.92 AND fraud precision >= 0.85 (else do NOT promote)
#   register -> fintelliguard.ml.fraud_scorer ; log the ml/serving pyfunc
# Point the serving endpoint at the new version (bundle var fraud_model_version) & redeploy.
```
Then complete step 4's Lambda wiring (`mosaic_endpoint_url`).

### 7. Knowledge Base ingestion
Upload the regulatory corpus and sync the Bedrock KB data source.
```bash
aws s3 cp agents/bedrock/kb/corpus/ s3://<kb-docs-bucket>/ --recursive
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id <kb-id> --data-source-id <ds-id> --region eu-central-1
```

### 8. Grafana — provisioning + dashboards
Set the data source env refs (no secrets in YAML), then load provisioning + JSON.
```bash
export DATABRICKS_HOST=... DATABRICKS_SQL_HTTP_PATH=... DATABRICKS_TOKEN=... PROMETHEUS_URL=...
# Mount dashboards/provisioning/*.yaml and dashboards/grafana/*.json into Grafana.
```

### 9. End-to-end smoke test
```bash
# Stream synthetic traffic through the live pipeline:
python -m simulator --sink kafka --duration 60   # simulator -> Kafka -> DLT -> gold -> serving
# Force a suspicious transaction and confirm the Bedrock Agent returns a grounded verdict.
# Run the copilot Agent Evaluation (tool-selection accuracy) against the live agent.
```

## Cost controls (keep active)

- Databricks clusters auto-terminate after 30 min idle; serving endpoints scale to zero.
- MSK off in dev (local Kafka); Bedrock **Haiku** in dev (Sonnet for final eval only).
- Always `terraform plan` before `apply`; `terraform destroy` per layer when idle.
- Budget target ~125–215 EUR/month managed; keep < 250 EUR total.

## Teardown (guarded, reverse order)

Use the `destroy.yml` workflow (typed-confirmation: type the exact environment name) or
manually, **in reverse**:
```bash
cd infra/bundles && databricks bundle destroy -t dev --auto-approve
cd agents/bedrock/terraform && terraform destroy
cd infra/databricks && terraform destroy
cd infra/aws && terraform destroy
# Leave the state backend (infra/aws/bootstrap) intact — it has prevent_destroy and
# holds remote state for every layer.
```
