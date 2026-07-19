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

Four, and only four:

| Secret | Why it cannot be derived |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | OIDC role — no static AWS keys. Output of the `bootstrap` layer. |
| `DATABRICKS_ACCOUNT_ID` | Identifies the Databricks account the workspace is created in. |
| `DATABRICKS_CLIENT_ID` | Service principal — the identity every layer and the bundle runs as. |
| `DATABRICKS_CLIENT_SECRET` | Its secret. |

**`DATABRICKS_HOST` and `DATABRICKS_TOKEN` are deliberately NOT secrets.** The workspace URL
is assigned by Databricks when `infra/databricks` creates the workspace, so a hand-pasted
host can only ever be wrong on the first deploy and unverified on every later one — both
workflows read it from that layer's `workspace_url` output instead. And the bundle
authenticates with the service principal's OAuth (`DATABRICKS_CLIENT_ID`/`_SECRET`) rather
than a PAT: the principal already exists, so a PAT would only add a second, longer-lived
credential for the same identity, minted by hand and rotated by nobody.

Configure GitHub Environments (`bootstrap`, `dev`, `prod`) with required reviewers for
approval gates.
