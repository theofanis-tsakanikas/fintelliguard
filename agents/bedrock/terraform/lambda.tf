# Action-group Lambda: packaged from agents/bedrock/lambda, running on the Mosaic-Lambda
# IAM role from infra/aws, inside the private subnets so it can reach Mosaic privately.

data "archive_file" "fraud_scoring" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/build/fraud_scoring.zip"

  # `archive_file` reads the FILESYSTEM, not the git index, so .gitignore does not apply.
  # Without this, `__pycache__` ships to the runtime: bytecode compiled by whatever Python
  # the developer last ran tests with (this tree had both cpython-312 and cpython-314
  # against a python3.12 runtime), and `source_code_hash` churns on every local test run —
  # producing phantom diffs in the very plan an approver is meant to review.
  excludes = ["__pycache__", "build"]
}

# Logs are evidence on a regulated path. Without an explicit group, the managed policy
# auto-creates one with retention "never expire" — unbounded cost, and it survives
# `terraform destroy` as an orphan.
resource "aws_cloudwatch_log_group" "fraud_scoring" {
  name              = "/aws/lambda/${local.name}-fraud-scoring"
  retention_in_days = var.lambda_log_retention_days
  kms_key_id        = local.aws.kms_key_arn

  tags = { Name = "${local.name}-fraud-scoring-logs" }
}

# A Tier-2 verdict that fails after retries must land somewhere a human can find it.
resource "aws_sqs_queue" "fraud_scoring_dlq" {
  name                              = "${local.name}-fraud-scoring-dlq"
  kms_master_key_id                 = local.aws.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 1209600 # 14 days — the max, and the audit window

  tags = { Name = "${local.name}-fraud-scoring-dlq" }
}

data "aws_iam_policy_document" "fraud_scoring_dlq" {
  statement {
    sid       = "AllowLambdaToPublishFailures"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.fraud_scoring_dlq.arn]

    principals {
      type        = "AWS"
      identifiers = [local.aws.lambda_role_arn]
    }
  }
}

resource "aws_sqs_queue_policy" "fraud_scoring_dlq" {
  queue_url = aws_sqs_queue.fraud_scoring_dlq.id
  policy    = data.aws_iam_policy_document.fraud_scoring_dlq.json
}

resource "aws_lambda_function" "fraud_scoring" {
  # Code signing (CKV_AWS_272) is a real gap, not a false positive: it needs an
  # `aws_signer_signing_profile` and a SIGNED artifact, and `archive_file` produces an
  # unsigned zip — wiring the config without the signing step would fail at apply. Skipped
  # HERE rather than in `.checkov.yml`, so it is a deferral attached to the resource that
  # defers it, and any future Lambda is still checked.
  #checkov:skip=CKV_AWS_272:Needs a signer profile + signed artifact; archive_file emits unsigned zips. Deferred, see docs/DEPLOY.md.

  function_name = "${local.name}-fraud-scoring"
  role          = local.aws.lambda_role_arn
  runtime       = var.lambda_runtime
  handler       = "handler.lambda_handler"
  filename      = data.archive_file.fraud_scoring.output_path

  source_code_hash = data.archive_file.fraud_scoring.output_base64sha256
  timeout          = 10
  memory_size      = 256

  # A Bedrock retry storm must not exhaust account-wide Lambda concurrency and take every
  # other function down with it. This is the blast-radius cap for the request path.
  reserved_concurrent_executions = var.lambda_reserved_concurrency

  # The env holds the secret ID and the Mosaic URL — encrypted with the project CMK rather
  # than the AWS-managed key everything else here deliberately avoids.
  kms_key_arn = local.aws.kms_key_arn

  dead_letter_config {
    target_arn = aws_sqs_queue.fraud_scoring_dlq.arn
  }

  # The Tier-2 path is the one CLAUDE.md requires to be traceable for audit. X-Ray is the
  # part of that which exists in AWS today (LangSmith covers only the healing graph).
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      MOSAIC_ENDPOINT_URL        = var.mosaic_endpoint_url
      DATABRICKS_TOKEN_SECRET_ID = local.databricks_secret_id
    }
  }

  vpc_config {
    subnet_ids         = local.aws.private_subnet_ids
    security_group_ids = [local.aws.lambda_security_group_id]
  }

  depends_on = [aws_cloudwatch_log_group.fraud_scoring]

  tags = { Name = "${local.name}-fraud-scoring" }
}

# Let the Bedrock agent invoke the Lambda (resource-based permission, least-privilege).
resource "aws_lambda_permission" "bedrock_invoke" {
  statement_id  = "AllowBedrockAgentInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fraud_scoring.function_name
  principal     = "bedrock.amazonaws.com"
  source_arn    = aws_bedrockagent_agent.this.agent_arn
}
