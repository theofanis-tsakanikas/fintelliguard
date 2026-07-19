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
      # AES256, not the CMK: the workspace's DBFS root is written by the Databricks
      # control plane, which needs its own grant on any customer-managed key. Wiring
      # `databricks_mws_customer_managed_keys` is the correct next step and is not done —
      # stated here rather than left to look like an oversight.
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Versioning on the DBFS root. The raw bucket and the KB corpus both have it; this one was
# the odd one out, and "this bucket happens to be less protected than the others" is not a
# decision anyone made — it is a decision nobody noticed, which is what the scanner is for.
resource "aws_s3_bucket_versioning" "root" {
  bucket = aws_s3_bucket.root.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "root" {
  bucket = aws_s3_bucket.root.id

  rule {
    id     = "expire-noncurrent-root-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.root]
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
# Databricks validates the cross-account role by ASSUMING it and checking its permissions.
# Two things have to be true before that call, and neither was guaranteed:
#
# 1. The POLICY must be attached. `databricks_mws_credentials` referenced only
#    `aws_iam_role.cross_account.arn`, so Terraform's implicit dependency was on the role
#    alone — it was free to run the validation concurrently with attaching the policy, and
#    validate a role that had no permissions yet.
# 2. IAM must have PROPAGATED. It is eventually consistent and global; a role that exists
#    to `GetRole` locally is not necessarily assumable from Databricks' account yet.
#
# Deploy run 1 failed exactly here — "Failed credential validation checks: please use a
# valid cross account IAM role" — 65 seconds after the role was created, with a trust
# policy that was entirely correct. The config was right; the ordering was not.
resource "time_sleep" "iam_propagation" {
  depends_on      = [aws_iam_role.cross_account, aws_iam_role_policy.cross_account]
  create_duration = "60s"
}

resource "databricks_mws_credentials" "this" {
  provider         = databricks.account
  credentials_name = "${local.name}-credentials"
  role_arn         = aws_iam_role.cross_account.arn

  depends_on = [time_sleep.iam_propagation]
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
