# Action-group Lambda: packaged from agents/bedrock/lambda, running on the Mosaic-Lambda
# IAM role from infra/aws, inside the private subnets so it can reach Mosaic privately.

data "archive_file" "fraud_scoring" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/build/fraud_scoring.zip"
}

resource "aws_lambda_function" "fraud_scoring" {
  function_name = "${local.name}-fraud-scoring"
  role          = local.aws.lambda_role_arn
  runtime       = var.lambda_runtime
  handler       = "handler.lambda_handler"
  filename      = data.archive_file.fraud_scoring.output_path

  source_code_hash = data.archive_file.fraud_scoring.output_base64sha256
  timeout          = 10
  memory_size      = 256

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
