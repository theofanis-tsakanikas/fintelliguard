locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition
  dns_suffix = data.aws_partition.current.dns_suffix

  # First `az_count` available AZs.
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Raw bucket: explicit override, else a globally-unique account-scoped name.
  raw_bucket = coalesce(var.raw_bucket_name, "${var.project}-raw-${local.account_id}")

  # Secret full paths: <project>/<environment>/<name>.
  secret_paths = { for n in var.managed_secret_names : n => "${var.project}/${var.environment}/${n}" }

  # MSK ARN patterns built from account/region (no hardcoded ids, no dependency on the
  # cost-guarded cluster resource). The trailing segments scope to this cluster's
  # resources — standard least-privilege MSK IAM, not a blanket wildcard.
  msk_cluster_name = "${local.name}-msk"
  msk_arn_base     = "arn:${local.partition}:kafka:${local.region}:${local.account_id}"
  msk_cluster_arn  = "${local.msk_arn_base}:cluster/${local.msk_cluster_name}/*"
  msk_topic_arn    = "${local.msk_arn_base}:topic/${local.msk_cluster_name}/*"
  msk_group_arn    = "${local.msk_arn_base}:group/${local.msk_cluster_name}/*"
}
