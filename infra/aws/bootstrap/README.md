# infra/aws/bootstrap/ — Terraform remote-state backend (one-time)

This standalone config solves the **chicken-and-egg** problem: Terraform needs a remote
backend to store state, but that backend (an S3 bucket + DynamoDB lock table) must
itself be created by Terraform. So this layer:

- uses **local state** (committed nowhere — see `.gitignore`), and
- creates the backend resources every *other* layer then consumes.

It runs **once per environment** and is essentially never destroyed.

## Prerequisites & setup notes

**Terraform — install via the HashiCorp tap, not `brew install terraform`.** The
`terraform` formula was removed from homebrew-core after the BSL license change, so the
plain command no longer installs it:

```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
terraform version   # verify
```

**Python tooling (ruff, pytest) runs from a local `.venv`.** Create and activate it once
per clone:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install ruff pytest
```

`make fmt|lint|test` auto-detect `.venv/bin/*` and fall back to the PATH binary, so the
Make targets work whether or not the venv is activated.

## What it creates

| Resource | Purpose |
|---|---|
| S3 bucket (`fintelliguard-tfstate`) | Stores remote state. **Versioned** (rollback), **encrypted** (AES256/SSE), **private** (all public access blocked). |
| DynamoDB table (`fintelliguard-tflock`) | State **locking** — prevents concurrent applies from corrupting state. |

Both carry `prevent_destroy` so a stray `terraform destroy` can't wipe shared state.

## Run it (one time)

```bash
cd infra/aws/bootstrap
terraform init                 # local backend
terraform plan                 # review first — always
terraform apply
terraform output               # note state_bucket / lock_table / region
```

`state_bucket_name` and `lock_table_name` must be **globally unique** (S3) — override
via `-var` or a `*.tfvars` file if the defaults are taken.

## How other layers consume it

Each layer (`infra/aws`, `infra/databricks`, `infra/bundles`) declares an **S3 backend**
pointing at these resources, with a **distinct `key`** so state stays isolated per layer:

```hcl
# e.g. infra/aws/backend.tf
terraform {
  backend "s3" {
    bucket         = "fintelliguard-tfstate"   # bootstrap output: state_bucket
    key            = "aws/terraform.tfstate"   # unique per layer
    region         = "eu-central-1"            # bootstrap output: region
    dynamodb_table = "fintelliguard-tflock"    # bootstrap output: lock_table
    encrypt        = true
  }
}
```

Layers never read each other's state directly — they exchange data through **outputs**
and `terraform_remote_state` **data sources**.

## Teardown

Intentionally hard. To dismantle, first empty the bucket's object versions, remove the
`prevent_destroy` lifecycle blocks, then `terraform destroy`. Only do this when retiring
the entire project.
