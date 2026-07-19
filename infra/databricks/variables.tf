# ---- Identity / region -------------------------------------------------------
variable "aws_region" {
  description = "AWS region for the workspace + its AWS prerequisites. Single-region footprint (eu-central-1)."
  type        = string
  default     = "eu-central-1"
}

variable "project" {
  description = "Project name; used as resource prefix, catalog name, and tag."
  type        = string
  default     = "fintelliguard"
}

variable "environment" {
  description = "Deployment environment (dev/stg/prod). Part of names and tags."
  type        = string
  default     = "dev"
}

# ---- Databricks account auth (NEVER hardcoded — env / Secrets Manager) --------
variable "databricks_account_host" {
  description = "Databricks accounts console URL."
  type        = string
  default     = "https://accounts.cloud.databricks.com"
}

variable "databricks_account_id" {
  description = "Databricks account id. Supply via TF_VAR_databricks_account_id (env) — also used as the cross-account IAM external id."
  type        = string
  default     = ""
}

variable "databricks_client_id" {
  description = "OAuth service-principal client id for account+workspace auth. Supply via env / Secrets Manager."
  type        = string
  default     = ""
}

variable "databricks_client_secret" {
  description = "OAuth service-principal secret. Supply via env / Secrets Manager — never commit."
  type        = string
  default     = ""
  sensitive   = true
}

# ---- Workspace ---------------------------------------------------------------
variable "workspace_name" {
  description = "Name for the Databricks workspace. Empty = `<project>-<environment>`."
  type        = string
  default     = ""
}

variable "root_bucket_name" {
  description = "Override the workspace DBFS root bucket name. Empty = `<project>-<environment>-dbfs-root-<account_id>`."
  type        = string
  default     = ""
}

variable "workspace_security_group_ids" {
  description = "Override data-plane security group ids for the customer-managed VPC. Empty = consume the dedicated Databricks data-plane SG from infra/aws remote state."
  type        = list(string)
  default     = []
}

variable "pricing_tier" {
  description = "Workspace pricing tier. PREMIUM is the minimum for Unity Catalog + cluster policies, but availability is a property of the Databricks SUBSCRIPTION — an account may offer only ENTERPRISE, and requesting an unavailable tier fails workspace creation. Set per deployment in dev.auto.tfvars."
  type        = string
  default     = "PREMIUM"
}

# ---- Unity Catalog -----------------------------------------------------------
variable "metastore_name" {
  description = "Unity Catalog metastore name. Empty = `<project>-<aws_region>`."
  type        = string
  default     = ""
}

variable "metastore_storage_root" {
  description = "Optional S3 URI for the metastore-level managed storage root. Empty = omit (use catalog-level storage instead)."
  type        = string
  default     = ""
}

variable "schemas" {
  description = "Schemas to create in the project catalog (medallion layers per CLAUDE.md)."
  type        = list(string)
  default     = ["bronze", "silver", "gold"]
}

variable "analyst_group_name" {
  description = <<-DESC
    Account-level group the fraud analysts belong to. MUST match `analyst_group` in
    `infra/bundles/databricks.yml` — the bundle grants SELECT on the gold schemas and the
    model registry to this name, and a mismatch fails the deploy at its last step with
    PRINCIPAL_DOES_NOT_EXIST.
  DESC
  type        = string
  default     = "fintelliguard-analysts"
}

variable "bucket_force_destroy" {
  description = <<-DESC
    Whether `terraform destroy` may empty the workspace root (DBFS) and Unity Catalog
    managed buckets before deleting them.

    Both buckets are VERSIONED. S3 refuses to delete a bucket that still holds objects —
    and for a versioned bucket, "empty" means every noncurrent version and delete marker
    too. With `force_destroy = false` the documented teardown is therefore not merely
    inconvenient, it is IMPOSSIBLE: the first destroy run failed with

        BucketNotEmpty: You must delete all versions in the bucket.

    on both buckets, after the workspace, metastore and catalog had already been deleted —
    leaving orphaned buckets and, because the destroy job stopped there, the entire
    `infra/aws` layer (MSK, VPC, NAT) standing and billing.

    Defaulted to `false` deliberately. These buckets hold the lakehouse's managed tables;
    self-emptying on destroy is a footgun, and a real environment should keep the guard and
    empty them by a reviewed, deliberate act. `dev.auto.tfvars` sets it true because the dev
    estate is explicitly disposable — built and torn down on demand — which is a property of
    that environment, not of the code.
  DESC
  type        = bool
  default     = false
}
