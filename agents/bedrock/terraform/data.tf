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

  foundation_model_arn = "arn:${local.partition}:bedrock:${local.region}::foundation-model/${var.foundation_model}"
  embedding_model_arn  = "arn:${local.partition}:bedrock:${local.region}::foundation-model/${var.embedding_model}"
}
