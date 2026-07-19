variable "aws_region" {
  description = "AWS region for the Bedrock zone."
  type        = string
  default     = "eu-central-1"
}

variable "project" {
  description = "Project name prefix/tag."
  type        = string
  default     = "fintelliguard"
}

variable "environment" {
  description = "Deployment environment."
  type        = string
  default     = "dev"
}

variable "foundation_model" {
  description = <<-DESC
    Bedrock foundation model id for the agent. Dev uses Claude Haiku 4.5; switch to
    anthropic.claude-sonnet-4-6 for final evaluation.

    Must be an id Bedrock actually serves in `aws_region` — verify with:
        aws bedrock get-foundation-model --region <r> --model-identifier <id>

    This was `anthropic.claude-haiku-4-5`, which does not resolve: Bedrock serves Haiku 4.5
    only under its dated, versioned id. Some newer models (opus-4-8, sonnet-5, sonnet-4-6)
    DO take a bare id, which is exactly what makes the inconsistency easy to miss — the
    shorter form looks right, and the failure surfaces at apply, after every other layer has
    already been created.
  DESC
  type        = string
  default     = "anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "embedding_model" {
  description = "Bedrock embedding model id for the Knowledge Base vector store."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "kb_vector_index_name" {
  description = "OpenSearch Serverless vector index name backing the Knowledge Base."
  type        = string
  default     = "fintelliguard-reg-index"
}

variable "mosaic_endpoint_url" {
  description = "Mosaic AI Model Serving endpoint URL the Lambda calls. Set at deploy (empty until then)."
  type        = string
  default     = ""
}

variable "lambda_runtime" {
  description = "Python runtime for the action-group Lambda."
  type        = string
  default     = "python3.12"
}

variable "databricks_secret_key" {
  description = "Key into infra/aws `secret_arns` for the Databricks token secret."
  type        = string
  default     = "databricks/token"
}

variable "lambda_log_retention_days" {
  description = <<-DESC
    Retention for the action-group Lambda's logs. Without an explicit log group AWS creates
    one that never expires; a year matches the audit lookback a financial regulator expects.
  DESC
  type        = number
  default     = 365
}

variable "lambda_reserved_concurrency" {
  description = <<-DESC
    Concurrency ceiling for the action-group Lambda. Tier 2 sees ~1% of transactions, so a
    small reservation is ample — and it stops a Bedrock retry storm exhausting account-wide
    Lambda concurrency and taking unrelated functions down with it.
  DESC
  type        = number
  default     = 20
}
