# Online feature store for the Tier-2 action group (and the Tier-3 copilot's get_fraud_score).
#
# The Bedrock action group takes IDS, not a feature vector (the cross-cloud contract in
# docs/bedrock-integration.md), so the Lambda must resolve (transaction_id, card_hash) -> the
# 15 features before it can call Mosaic. A production system serves these from the Mosaic
# online Feature Store; for the demo the store is a small, KMS-encrypted JSON object holding a
# few REPRESENTATIVE transactions. The seam (id -> features) is identical, so swapping the
# backing store is a change to `S3OnlineFeatureStore` alone.
#
# Seeded via Terraform (like the raw-prefix marker) rather than a deploy step: no gold read, no
# SQL warehouse, no cross-cloud creds on a cluster — the values are representative, and the
# object is recreated on every apply. It lives under `online-features/`, NOT `raw/ieee-cis/`,
# so the DLT Auto Loader never sees it.

locals {
  online_feature_key = "online-features/transactions.json"

  # Two representative transactions: one high-risk (mirrors the verified 0.888 smoke test) and
  # one benign. `card_hash` is a secondary key the Lambda validates. The 14 model features use
  # the canonical dtypes from docs/features.md.
  online_features = {
    "txn_demo_fraud_001" = {
      card_hash              = "card_demo_hi_risk"
      amount_usd             = 250.0
      amount_log             = 5.525453
      amount_zscore          = 3.5
      txn_velocity_1h        = 9
      txn_velocity_24h       = 12
      amount_sum_1h          = 2200.0
      distinct_merchants_24h = 4
      card_age_days          = 30
      device_seen_before     = false
      device_txn_count_24h   = 5
      country_mismatch       = true
      distinct_countries_24h = 2
      mcc_risk_tier          = 5
      is_unusual_hour        = true
    }
    "txn_demo_legit_001" = {
      card_hash              = "card_demo_normal"
      amount_usd             = 19.99
      amount_log             = 3.044522
      amount_zscore          = 0.1
      txn_velocity_1h        = 1
      txn_velocity_24h       = 3
      amount_sum_1h          = 55.0
      distinct_merchants_24h = 2
      card_age_days          = 800
      device_seen_before     = true
      device_txn_count_24h   = 40
      country_mismatch       = false
      distinct_countries_24h = 1
      mcc_risk_tier          = 1
      is_unusual_hour        = false
    }
  }
}

resource "aws_s3_object" "online_features" {
  bucket  = aws_s3_bucket.raw.id
  key     = local.online_feature_key
  content = jsonencode(local.online_features)

  # Same estate CMK as the rest of the bucket, so the Lambda role (granted decrypt on this key)
  # can read it and nothing else can.
  server_side_encryption = "aws:kms"
  kms_key_id             = aws_kms_key.main.arn

  depends_on = [aws_s3_bucket_server_side_encryption_configuration.raw]
}
