# Networking comes from infra/aws (private control, PrivateLink alignment) — consumed
# read-only via remote state, never by cross-layer state access.
data "terraform_remote_state" "aws" {
  backend = "s3"

  config = {
    bucket = "fintelliguard-tfstate"
    key    = "infra/aws/terraform.tfstate"
    region = "eu-central-1"
  }
}

# Account id for unique bucket naming. Region/credentials from the aws provider.
data "aws_caller_identity" "current" {}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  aws        = data.terraform_remote_state.aws.outputs

  workspace_name = coalesce(var.workspace_name, local.name)
  root_bucket    = coalesce(var.root_bucket_name, "${local.name}-dbfs-root-${local.account_id}")
  metastore_name = coalesce(var.metastore_name, "${var.project}-${var.aws_region}")

  # Customer-managed VPC inputs from infra/aws remote state.
  vpc_id     = local.aws.vpc_id
  subnet_ids = local.aws.private_subnet_ids

  # Data-plane SGs: operator override, else the infra/aws SG that fronts the
  # interface/PrivateLink endpoints (see README networking note).
  workspace_security_group_ids = length(var.workspace_security_group_ids) > 0 ? var.workspace_security_group_ids : [local.aws.endpoints_security_group_id]
}
