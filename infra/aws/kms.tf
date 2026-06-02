# Customer-managed key for encryption at rest across S3, Secrets Manager, and MSK.
# Key policy is the balanced AWS-recommended shape: root administers the key, and the
# account may USE it only via the specific services (kms:ViaService), plus grants for
# AWS-managed resources (MSK/EBS). "Resource = *" in a key policy scopes to THIS key.

data "aws_iam_policy_document" "kms" {
  # Root account administers the key (prevents an unmanageable/orphaned key).
  statement {
    sid       = "EnableRootAdministration"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  # Use the key for crypto operations, but only through the intended services.
  statement {
    sid    = "AllowServiceUse"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values = [
        "s3.${local.region}.${local.dns_suffix}",
        "secretsmanager.${local.region}.${local.dns_suffix}",
        "kafka.${local.region}.${local.dns_suffix}",
      ]
    }
  }

  # Grants for AWS-managed resources that encrypt on your behalf (e.g. MSK storage).
  statement {
    sid    = "AllowGrantsForAWSResources"
    effect = "Allow"
    actions = [
      "kms:CreateGrant",
      "kms:ListGrants",
      "kms:RevokeGrant",
    ]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }

    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values   = ["true"]
    }
  }
}

resource "aws_kms_key" "main" {
  description             = "${local.name} encryption-at-rest CMK (S3, Secrets, MSK)"
  policy                  = data.aws_iam_policy_document.kms.json
  enable_key_rotation     = true
  deletion_window_in_days = 7

  tags = { Name = "${local.name}-cmk" }
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.main.key_id
}
