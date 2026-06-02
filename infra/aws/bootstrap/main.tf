# Terraform remote-state backend.
#
# Creates the two resources every other TF layer (infra/aws, infra/databricks,
# infra/bundles) shares for remote state:
#   * a versioned, encrypted, private S3 bucket  -> stores state files
#   * a DynamoDB table                            -> state locking (prevents
#                                                    concurrent/corrupting applies)
#
# Run ONCE. See README.md.

# ---- S3 bucket: state storage ----------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket = var.state_bucket_name

  # State is precious + hard to recreate: guard against accidental `terraform destroy`.
  lifecycle {
    prevent_destroy = true
  }
}

# Versioning: keep history so a bad apply can be rolled back.
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Encryption at rest (SSE). KMS-managed keys come with the AWS layer; the backend
# uses S3-managed (AES256) to stay dependency-free at bootstrap time.
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# State must never be public.
resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---- DynamoDB: state locking -----------------------------------------------
resource "aws_dynamodb_table" "tflock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  lifecycle {
    prevent_destroy = true
  }
}
