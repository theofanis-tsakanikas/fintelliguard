output "workspace_id" {
  description = "Databricks workspace id."
  value       = databricks_mws_workspaces.this.workspace_id
}

output "workspace_url" {
  description = "Databricks workspace URL (host for the workspace provider / DABs)."
  value       = databricks_mws_workspaces.this.workspace_url
}

output "metastore_id" {
  description = "Unity Catalog metastore id."
  value       = databricks_metastore.this.id
}

output "catalog_name" {
  description = "Project catalog name."
  value       = databricks_catalog.fintelliguard.name
}

output "schema_names" {
  description = "Catalog-qualified schema names."
  value       = [for s in databricks_schema.medallion : "${databricks_catalog.fintelliguard.name}.${s.name}"]
}

output "cross_account_role_arn" {
  description = "Cross-account IAM role ARN Databricks assumes."
  value       = aws_iam_role.cross_account.arn
}

output "root_bucket_name" {
  description = "Workspace DBFS root bucket."
  value       = aws_s3_bucket.root.bucket
}

output "network_id" {
  description = "Databricks customer-managed network id (infra/aws VPC)."
  value       = databricks_mws_networks.this.network_id
}

output "msk_instance_profile_id" {
  description = "Registered Databricks instance-profile id for a classic cluster to reach MSK with IAM."
  value       = databricks_instance_profile.msk_access.id
}
