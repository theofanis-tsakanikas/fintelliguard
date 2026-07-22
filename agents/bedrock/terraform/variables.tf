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
    The model the agent INVOKES — an INFERENCE PROFILE id (the `eu.` cross-region system
    profile), never a bare model id: newer models have no on-demand throughput on the bare id
    ("Invocation ... with on-demand throughput isn't supported. Retry ... with an inference
    profile"), which the agent surfaces as an opaque accessDenied.

    Amazon Nova Lite, not Claude Haiku 4.5, is the default — for a diagnosed reason. A Bedrock
    agent invokes its model with RESPONSE STREAMING, and streaming Anthropic models in this
    account fails with

        ResourceNotFoundException: Model use case details have not been submitted for this
        account.

    i.e. Anthropic model access on Bedrock is gated behind a one-time use-case submission in
    the console (a MANUAL step, not IaC). Amazon's first-party Nova has no such gate and streams
    out of the box (verified live with invoke_model_with_response_stream). To run the agent on
    Claude Haiku instead, submit the Anthropic use-case in the Bedrock console and set this to
    `eu.anthropic.claude-haiku-4-5-20251001-v1:0` (+ the base id below). Nova is also cheaper.

    Verify a profile exists with `aws bedrock list-inference-profiles`.
  DESC
  type        = string
  default     = "eu.amazon.nova-lite-v1:0"
}

variable "foundation_model_base_id" {
  description = <<-DESC
    The bare foundation model the inference profile routes to. Invoking via an inference
    profile requires bedrock:InvokeModel(WithResponseStream) on BOTH the profile ARN and the
    foundation-model ARNs in the regions it can route to, so this is granted alongside the
    profile in iam.tf.
  DESC
  type        = string
  default     = "amazon.nova-lite-v1:0"
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
