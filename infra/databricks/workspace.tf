# =============================================================================
# Databricks workspace on a CUSTOMER-MANAGED VPC (networking from infra/aws).
#
# AWS prerequisites (cross-account role + root bucket) are created here from the
# Databricks-provided policy documents, then registered with the account provider as
# credentials / storage / network configurations and assembled into the workspace.
# =============================================================================

# ---- Cross-account IAM role Databricks assumes ------------------------------
data "databricks_aws_assume_role_policy" "this" {
  external_id = var.databricks_account_id
}

data "databricks_aws_crossaccount_policy" "this" {}

resource "aws_iam_role" "cross_account" {
  name               = "${local.name}-databricks-crossaccount"
  assume_role_policy = data.databricks_aws_assume_role_policy.this.json
  tags               = { Name = "${local.name}-databricks-crossaccount" }
}

resource "aws_iam_role_policy" "cross_account" {
  name   = "databricks-crossaccount"
  role   = aws_iam_role.cross_account.id
  policy = data.databricks_aws_crossaccount_policy.this.json
}

# ---- Workspace root (DBFS) bucket -------------------------------------------
resource "aws_s3_bucket" "root" {
  bucket        = local.root_bucket
  force_destroy = false
  tags          = { Name = local.root_bucket }
}

resource "aws_s3_bucket_public_access_block" "root" {
  bucket = aws_s3_bucket.root.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "root" {
  bucket = aws_s3_bucket.root.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

data "databricks_aws_bucket_policy" "root" {
  bucket = aws_s3_bucket.root.bucket
}

resource "aws_s3_bucket_policy" "root" {
  bucket = aws_s3_bucket.root.id
  policy = data.databricks_aws_bucket_policy.root.json

  # Ensure public-access block lands before the Databricks grant.
  depends_on = [aws_s3_bucket_public_access_block.root]
}

# ---- Account-level workspace configurations ---------------------------------
resource "databricks_mws_credentials" "this" {
  provider         = databricks.account
  credentials_name = "${local.name}-credentials"
  role_arn         = aws_iam_role.cross_account.arn
}

resource "databricks_mws_storage_configurations" "this" {
  provider                   = databricks.account
  account_id                 = var.databricks_account_id
  storage_configuration_name = "${local.name}-storage"
  bucket_name                = aws_s3_bucket.root.bucket
}

# Customer-managed VPC: consumes infra/aws VPC, private subnets, and SGs.
# account_id is still a required argument on this resource (provider-level value is
# inherited by the others, where the per-resource argument is deprecated).
resource "databricks_mws_networks" "this" {
  provider           = databricks.account
  account_id         = var.databricks_account_id
  network_name       = "${local.name}-network"
  vpc_id             = local.vpc_id
  subnet_ids         = local.subnet_ids
  security_group_ids = local.workspace_security_group_ids
}

resource "databricks_mws_workspaces" "this" {
  provider                 = databricks.account
  account_id               = var.databricks_account_id
  workspace_name           = local.workspace_name
  aws_region               = var.aws_region
  pricing_tier             = var.pricing_tier
  credentials_id           = databricks_mws_credentials.this.credentials_id
  storage_configuration_id = databricks_mws_storage_configurations.this.storage_configuration_id
  network_id               = databricks_mws_networks.this.network_id
}
