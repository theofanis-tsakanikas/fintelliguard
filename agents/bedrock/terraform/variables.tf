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
    The model the agent INVOKES — an INFERENCE PROFILE id for Claude Haiku 4.5, not the bare
    model id. Haiku 4.5 has no on-demand throughput on the bare id; invoking it fails with

        ValidationException: Invocation of model ID anthropic.claude-haiku-4-5-...-v1:0 with
        on-demand throughput isn't supported. Retry ... with an inference profile.

    which the AGENT surfaces as an opaque accessDenied at InvokeAgent time — the model resolves
    at apply (get-foundation-model succeeds) and the deploy goes green, then every verdict is
    denied (deploy run 29894311393). The `eu.` cross-region system profile is the invocation
    target; verify with `aws bedrock list-inference-profiles`. `foundation_model_base_id` below
    is the underlying model the profile routes to (needed for the IAM grant).

    Switch to a Sonnet inference profile for final evaluation.
  DESC
  type        = string
  default     = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "foundation_model_base_id" {
  description = <<-DESC
    The bare foundation model the inference profile routes to. Invoking via an inference
    profile requires bedrock:InvokeModel on BOTH the profile ARN and the foundation-model ARNs
    in the regions it can route to, so this is granted alongside the profile in iam.tf.
  DESC
  type        = string
  default     = "anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "inference_profile_regions" {
  description = "Regions the EU cross-region inference profile can route to (for the FM ARN grants)."
  type        = list(string)
  default     = ["eu-central-1", "eu-west-1", "eu-west-3", "eu-north-1"]
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
