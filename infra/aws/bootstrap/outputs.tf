output "state_bucket" {
  description = "S3 bucket holding Terraform remote state — set as `backend.bucket`."
  value       = aws_s3_bucket.tfstate.id
}

output "lock_table" {
  description = "DynamoDB table for state locking — set as `backend.dynamodb_table`."
  value       = aws_dynamodb_table.tflock.name
}

output "region" {
  description = "Region of the backend resources — set as `backend.region`."
  value       = var.aws_region
}

output "github_deploy_role_arn" {
  description = "Set this as the AWS_DEPLOY_ROLE_ARN GitHub secret — the OIDC role the workflows assume."
  value       = aws_iam_role.deploy.arn
}
