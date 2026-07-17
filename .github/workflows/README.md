# CI/CD workflows

| Workflow | Trigger | Needs cloud creds | Status |
|---|---|---|---|
| `ci.yml` | `pull_request` + push to `main` | No | **Active** — reproduces every local gate |
| `bootstrap.yml` | `workflow_dispatch` | Yes (AWS) | **Manual, gated, deferred** |
| `deploy.yml` | `workflow_dispatch` | Yes (AWS + Databricks) | **Manual, gated, deferred** |
| `destroy.yml` | `workflow_dispatch` | Yes (AWS + Databricks) | **Manual, gated, deferred** |

## `ci.yml` — PR validation (no cloud)

Reproduces the exact local gates: Python 3.11 + Java 17 + OpenMP + the `[dev]` extras,
then `ruff check`, `ruff format --check`, `pytest` (full suite), `terraform fmt -check` +
offline `validate` for every TF layer (`infra/aws`, `infra/aws/bootstrap`,
`infra/databricks`, `agents/bedrock/terraform`), and `databricks bundle validate` (schema
only — workspace auth is treated as the expected deferred step).

## Gated / deferred (manual `workflow_dispatch` only)

These are **defined but not auto-triggered**. They use cloud credentials from **GitHub
secrets** and run only when a maintainer dispatches them. Provisioning is deferred to the
deploy phase.

- **`bootstrap.yml`** — applies `infra/aws/bootstrap` (the Terraform remote-state
  backend). Requires typing `bootstrap` to confirm; runs in the `bootstrap` environment.
- **`deploy.yml`** — ordered apply with an `environment` input
  (`dev`/`prod`): **infra/aws → infra/databricks → agents/bedrock/terraform →
  infra/bundles (DABs deploy)**. Each layer consumes the previous layer's remote-state
  outputs, so the order is load-bearing.
- **`destroy.yml`** — **guarded**: requires typing the **exact environment name** as a
  confirmation input before any `terraform destroy`. Tears down in reverse order; the
  state backend (`bootstrap`) is intentionally left intact.

### Required secrets (deploy phase)

`AWS_DEPLOY_ROLE_ARN` (OIDC — no static AWS keys), `DATABRICKS_HOST`, `DATABRICKS_TOKEN`,
`DATABRICKS_ACCOUNT_ID`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`. Configure
GitHub Environments (`bootstrap`, `dev`, `prod`) with required reviewers for approval
gates.
