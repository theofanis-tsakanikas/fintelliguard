# Raw landing bucket: IEEE-CIS batch files + raw stream captures.
# Versioned, KMS-encrypted, fully private, with lifecycle expiry to cap storage cost.

resource "aws_s3_bucket" "raw" {
  bucket = local.raw_bucket
  tags   = { Name = local.raw_bucket }
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket = aws_s3_bucket.raw.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    id     = "expire-raw"
    status = "Enabled"

    filter {} # whole bucket

    expiration {
      days = var.raw_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.raw_noncurrent_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
