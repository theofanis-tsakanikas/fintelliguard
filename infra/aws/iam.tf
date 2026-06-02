# =============================================================================
# Least-privilege IAM. Authored inline policies are scoped to specific ARNs — no
# Action:* and no Resource:*. The only wildcards in effect come from the two AWS
# *managed* Lambda execution policies (basic logging + VPC ENI lifecycle), whose ENI
# actions REQUIRE Resource:* because the network interface does not pre-exist. Using
# the managed policies is the standard, audited way to grant exactly that.
# =============================================================================

# ---- (a) Lambda action-group role: calls Mosaic Model Serving -----------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.${local.dns_suffix}"]
    }
  }
}

resource "aws_iam_role" "lambda_mosaic" {
  name               = "${local.name}-lambda-mosaic"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { Name = "${local.name}-lambda-mosaic" }
}

# CloudWatch Logs + VPC networking for a Lambda running in private subnets.
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_mosaic.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda_mosaic.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Scoped: read the project secrets (Databricks token) + decrypt them with the CMK.
data "aws_iam_policy_document" "lambda_inline" {
  statement {
    sid       = "ReadProjectSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for s in aws_secretsmanager_secret.this : s.arn]
  }

  statement {
    sid       = "DecryptWithCMK"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.main.arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${local.region}.${local.dns_suffix}"]
    }
  }
}

resource "aws_iam_role_policy" "lambda_inline" {
  name   = "mosaic-access"
  role   = aws_iam_role.lambda_mosaic.id
  policy = data.aws_iam_policy_document.lambda_inline.json
}

# ---- (b) MSK access role: producers/consumers using IAM SASL auth -------------
# Assumable by EC2/ECS workloads (simulator, Spark consumers). Scoped to this
# cluster's resources via kafka-cluster ARNs (built in locals from account/region).
data "aws_iam_policy_document" "msk_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.${local.dns_suffix}"]
    }
  }
}

resource "aws_iam_role" "msk_access" {
  name               = "${local.name}-msk-access"
  assume_role_policy = data.aws_iam_policy_document.msk_assume.json
  tags               = { Name = "${local.name}-msk-access" }
}

data "aws_iam_policy_document" "msk_access" {
  statement {
    sid    = "ConnectCluster"
    effect = "Allow"
    actions = [
      "kafka-cluster:Connect",
      "kafka-cluster:DescribeCluster",
    ]
    resources = [local.msk_cluster_arn]
  }

  statement {
    sid    = "ProduceConsumeTopics"
    effect = "Allow"
    actions = [
      "kafka-cluster:CreateTopic",
      "kafka-cluster:DescribeTopic",
      "kafka-cluster:WriteData",
      "kafka-cluster:ReadData",
    ]
    resources = [local.msk_topic_arn]
  }

  statement {
    sid    = "ConsumerGroups"
    effect = "Allow"
    actions = [
      "kafka-cluster:AlterGroup",
      "kafka-cluster:DescribeGroup",
    ]
    resources = [local.msk_group_arn]
  }
}

resource "aws_iam_role_policy" "msk_access" {
  name   = "msk-iam-access"
  role   = aws_iam_role.msk_access.id
  policy = data.aws_iam_policy_document.msk_access.json
}
