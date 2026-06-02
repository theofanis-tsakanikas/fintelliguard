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
  description = "Data-plane security group ids for the customer-managed VPC. Empty = consume the infra/aws endpoints SG from remote state (see README networking note)."
  type        = list(string)
  default     = []
}

variable "pricing_tier" {
  description = "Workspace pricing tier. PREMIUM is required for Unity Catalog + cluster policies."
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
