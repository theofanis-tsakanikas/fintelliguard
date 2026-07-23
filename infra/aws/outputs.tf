# Consumed by later layers (infra/databricks, infra/bundles) and the Lambda
# action-group via `terraform_remote_state` against this layer's state.

# ---- Networking --------------------------------------------------------------
output "vpc_id" {
  description = "VPC id."
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "VPC CIDR block."
  value       = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet ids (NAT, ingress)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet ids (Lambda, MSK, endpoints)."
  value       = aws_subnet.private[*].id
}

output "lambda_security_group_id" {
  description = "SG for the Mosaic-calling Lambda action-group."
  value       = aws_security_group.lambda.id
}

output "msk_security_group_id" {
  description = "SG for MSK broker access."
  value       = aws_security_group.msk.id
}

output "endpoints_security_group_id" {
  description = "SG fronting the interface VPC endpoints."
  value       = aws_security_group.endpoints.id
}

output "databricks_data_plane_sg_id" {
  description = "Databricks customer-managed VPC data-plane SG (SCC / back-end PrivateLink)."
  value       = aws_security_group.databricks_data_plane.id
}

# ---- KMS / S3 ----------------------------------------------------------------
output "kms_key_arn" {
  description = "Customer-managed key ARN (S3, Secrets, MSK at rest)."
  value       = aws_kms_key.main.arn
}

output "kms_key_alias" {
  description = "Customer-managed key alias."
  value       = aws_kms_alias.main.name
}

output "raw_bucket_name" {
  description = "Raw landing bucket name."
  value       = aws_s3_bucket.raw.id
}

output "raw_bucket_arn" {
  description = "Raw landing bucket ARN."
  value       = aws_s3_bucket.raw.arn
}

output "online_feature_key" {
  description = "S3 key of the online-feature object the Tier-2 Lambda resolves ids against."
  value       = aws_s3_object.online_features.key
}

# ---- Secrets / IAM -----------------------------------------------------------
output "secret_arns" {
  description = "Map of managed secret name -> ARN (placeholders; values injected at runtime)."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "lambda_role_arn" {
  description = "IAM role ARN for the Lambda action-group."
  value       = aws_iam_role.lambda_mosaic.arn
}

output "msk_access_role_arn" {
  description = "IAM role ARN for MSK IAM-auth producers/consumers."
  value       = aws_iam_role.msk_access.arn
}

output "msk_access_instance_profile_arn" {
  description = "Instance-profile ARN for a Databricks classic cluster to authenticate to MSK with IAM."
  value       = aws_iam_instance_profile.msk_access.arn
}

# ---- MSK (null unless enable_msk = true) -------------------------------------
output "msk_cluster_arn" {
  description = "MSK cluster ARN, or null when enable_msk = false."
  value       = one(aws_msk_cluster.this[*].arn)
}

output "msk_bootstrap_brokers_sasl_iam" {
  description = "MSK IAM SASL bootstrap brokers, or null when enable_msk = false."
  value       = one(aws_msk_cluster.this[*].bootstrap_brokers_sasl_iam)
}

# ---- Generator (null unless enable_msk = true) -------------------------------
output "generator_ecr_repository_url" {
  description = "ECR repo the Fargate generator image is pushed to, or null when enable_msk = false."
  value       = one(aws_ecr_repository.generator[*].repository_url)
}

output "generator_ecs_cluster" {
  description = "ECS cluster the generator task runs in, or null when enable_msk = false."
  value       = one(aws_ecs_cluster.streaming[*].name)
}

output "generator_task_family" {
  description = "Generator task-definition family (for `aws ecs run-task`), or null when enable_msk = false."
  value       = one(aws_ecs_task_definition.generator[*].family)
}

output "scorer_task_family" {
  description = "Scorer (stream consumer) task-definition family, or null when enable_msk = false."
  value       = one(aws_ecs_task_definition.scorer[*].family)
}

output "generator_security_group_id" {
  description = "Security group for the Fargate generator task."
  value       = aws_security_group.generator.id
}

# ---- Context -----------------------------------------------------------------
output "region" {
  description = "Region this layer is deployed in."
  value       = local.region
}
