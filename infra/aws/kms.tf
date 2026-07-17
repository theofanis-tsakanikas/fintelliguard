# Customer-managed key for encryption at rest across S3, Secrets Manager, and MSK.
# Key policy is the balanced AWS-recommended shape: root administers the key, and the
# account may USE it only via the specific services (kms:ViaService), plus grants for
# AWS-managed resources (MSK/EBS). "Resource = *" in a key policy scopes to THIS key.

data "aws_iam_policy_document" "kms" {
  # A KMS *key policy*'s Resource is the key it is attached to; AWS requires "*" to mean
  # exactly that, and there is no narrower value to write. Scoped HERE rather than in
  # `.checkov.yml`, because a repo-wide `skip-check` would also hide the next genuine
  # `Resource: "*"` anyone adds — which is how "no authored wildcards" became a comment
  # sitting two lines above `aoss:*`.
  #checkov:skip=CKV_AWS_109:Key-policy Resource is the key itself; "*" is the only valid value here.
  #checkov:skip=CKV_AWS_111:Root administration of the key is the AWS-recommended shape; use is scoped by kms:ViaService.
  #checkov:skip=CKV_AWS_356:Same — a key policy cannot name its own key by ARN.

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
