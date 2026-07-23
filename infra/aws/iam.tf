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

# Scoped: read the ONE secret the Lambda needs (Databricks token) + decrypt it with the CMK.
data "aws_iam_policy_document" "lambda_inline" {
  statement {
    sid     = "ReadDatabricksToken"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    # The Databricks token ARN only — was `for s in ...this : s.arn`, which is EVERY project
    # secret including `langsmith/api-key`. `secrets.tf` even said "the Lambda's IAM scopes
    # that read to the databricks/token secret ARN only", so this was a comment denying the
    # over-grant it sat above. The Lambda has no business reading the LangSmith key.
    resources = [aws_secretsmanager_secret.this["databricks/token"].arn]
  }

  # Read the ONE online-feature object the action group resolves ids against — scoped to that
  # exact key, not the bucket, so the Lambda cannot read IEEE-CIS or anything else in raw/.
  statement {
    sid       = "ReadOnlineFeatureObject"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.raw.arn}/${local.online_feature_key}"]
  }

  statement {
    sid       = "DecryptWithCMK"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.main.arn]

    # Both services the Lambda decrypts through: Secrets Manager (the Databricks token) and S3
    # (the online-feature object). Both are CMK-encrypted; without the S3 grant the object read
    # fails with an AccessDenied that looks like an S3 policy gap.
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values = [
        "secretsmanager.${local.region}.${local.dns_suffix}",
        "s3.${local.region}.${local.dns_suffix}",
      ]
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
#
# BOTH principals, and the second one is not decoration:
#   ec2        — the role is wrapped in an instance profile that Databricks classic clusters
#                carry, which is how the Spark/probe side authenticates to MSK with IAM.
#   ecs-tasks  — the same role is the task_role for the in-VPC generator and scorer. Fargate
#                assumes it as `ecs-tasks.amazonaws.com`, and with only `ec2` in the trust
#                RunTask fails outright: "ECS was unable to assume the role ... verify that
#                the role being passed has the proper trust relationship". The comment above
#                claimed EC2/ECS from the start; the trust policy only ever said EC2.
data "aws_iam_policy_document" "msk_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.${local.dns_suffix}", "ecs-tasks.${local.dns_suffix}"]
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
