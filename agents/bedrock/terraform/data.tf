# Consume infra/aws outputs (KMS, Mosaic-Lambda IAM role, secrets, private networking)
# read-only via remote state — never cross-layer state access.
data "terraform_remote_state" "aws" {
  backend = "s3"

  config = {
    bucket = "fintelliguard-tfstate"
    key    = "infra/aws/terraform.tfstate"
    region = "eu-central-1"
  }
}

data "aws_caller_identity" "current" {}

# The identity actually running `terraform apply`, resolved from the assumed-role SESSION
# arn (`.../assumed-role/<role>/<session>`) to the underlying ROLE arn. An AOSS data access
# policy matches on the role, so the session form never matches and the principal is simply
# absent from every policy — which AOSS answers with a bare 401.
data "aws_iam_session_context" "current" {
  arn = data.aws_caller_identity.current.arn
}

data "aws_partition" "current" {}

data "aws_region" "current" {}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  aws        = data.terraform_remote_state.aws.outputs

  collection_name      = "${var.project}-reg"
  databricks_secret_id = local.aws.secret_arns[var.databricks_secret_key]

  # The agent invokes an INFERENCE PROFILE (Haiku 4.5 has no on-demand throughput on the bare
  # model id). The profile ARN is account-scoped; invoking through it also requires
  # bedrock:InvokeModel on the base model in every region the cross-region profile can route to.
  foundation_model_arn = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:inference-profile/${var.foundation_model}"
  foundation_model_invoke_arns = concat(
    [local.foundation_model_arn],
    [for r in var.inference_profile_regions :
      "arn:${local.partition}:bedrock:${r}::foundation-model/${var.foundation_model_base_id}"
    ]
  )
  embedding_model_arn = "arn:${local.partition}:bedrock:${local.region}::foundation-model/${var.embedding_model}"
}
