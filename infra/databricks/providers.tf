# AWS provider — creates the workspace's AWS prerequisites (cross-account role, root
# bucket) in the same region as infra/aws. Credentials come from ~/.aws (never here).
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      Layer       = "databricks"
      ManagedBy   = "terraform"
    }
  }
}

# Account-level Databricks provider — creates the workspace (mws_*) and the Unity
# Catalog metastore + assignment. Auth via OAuth service principal (client_id/secret),
# sourced from env / Secrets Manager at run time. See README "Account auth".
provider "databricks" {
  alias         = "account"
  host          = var.databricks_account_host
  account_id    = var.databricks_account_id
  client_id     = var.databricks_client_id
  client_secret = var.databricks_client_secret
}

# Workspace-level Databricks provider — creates the catalog + schemas. Host is the URL
# of the workspace this layer provisions; same OAuth SP (added to the workspace).
provider "databricks" {
  alias         = "workspace"
  host          = databricks_mws_workspaces.this.workspace_url
  client_id     = var.databricks_client_id
  client_secret = var.databricks_client_secret
}
