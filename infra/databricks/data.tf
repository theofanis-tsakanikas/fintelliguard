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

  _aws_remote = data.terraform_remote_state.aws.outputs

  # Every upstream read is wrapped, so this layer stays PLANNABLE after infra/aws is gone.
  #
  # Terraform evaluates the whole configuration to build a destroy plan, including
  # expressions belonging to resources that are already destroyed. Reading
  # `data.terraform_remote_state.aws.outputs.vpc_id` directly therefore fails with
  #
  #     Error: Unsupported attribute
  #
  # the moment infra/aws has no outputs — and the failure is total: the layer cannot plan,
  # so its OWN surviving resources can never be destroyed either. That is exactly what
  # happened in run 29687321500. The destroy job (correctly) does not stop at a failing
  # layer, because halting left MSK/VPC/NAT billing; so infra/aws came down while two
  # buckets still stood in this layer's state, and this layer wedged permanently.
  #
  # A teardown must not be able to strand a layer. `try(..., null)` makes a destroyed
  # upstream a survivable condition rather than a parse-level dead end.
  #
  # The cost, stated plainly: on an APPLY where infra/aws has genuinely not been applied
  # yet, these resolve to null and the failure surfaces later — at workspace creation, with
  # a Databricks-side message — instead of immediately as "Unsupported attribute". That is
  # a worse first-deploy experience traded for a teardown that cannot deadlock. The deploy
  # workflow already applies infra/aws first, so the null case means someone ran this layer
  # out of order; the deadlock case cost real money.
  aws = {
    vpc_id                      = try(local._aws_remote.vpc_id, null)
    private_subnet_ids          = try(local._aws_remote.private_subnet_ids, null)
    databricks_data_plane_sg_id = try(local._aws_remote.databricks_data_plane_sg_id, null)
    kms_key_arn                 = try(local._aws_remote.kms_key_arn, null)
  }

  workspace_name = coalesce(var.workspace_name, local.name)
  root_bucket    = coalesce(var.root_bucket_name, "${local.name}-dbfs-root-${local.account_id}")
  metastore_name = coalesce(var.metastore_name, "${var.project}-${var.aws_region}")

  # Customer-managed VPC inputs from infra/aws remote state.
  vpc_id     = local.aws.vpc_id
  subnet_ids = local.aws.private_subnet_ids

  # Data-plane SGs: operator override, else the dedicated Databricks data-plane SG
  # provisioned in infra/aws (SCC / back-end PrivateLink rules).
  workspace_security_group_ids = length(var.workspace_security_group_ids) > 0 ? var.workspace_security_group_ids : [local.aws.databricks_data_plane_sg_id]
}
