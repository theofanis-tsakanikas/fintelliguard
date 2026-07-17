variable "aws_region" {
  description = "AWS region for the Terraform state backend resources."
  type        = string
  default     = "eu-central-1"
}

variable "state_bucket_name" {
  description = "Globally-unique S3 bucket name for Terraform remote state."
  type        = string
  default     = "fintelliguard-tfstate"
}

variable "lock_table_name" {
  description = "DynamoDB table name for Terraform state locking."
  type        = string
  default     = "fintelliguard-tflock"
}

variable "github_repository" {
  description = "owner/repo that GitHub Actions OIDC is scoped to. The sub condition pins to this."
  type        = string
  default     = "theofanis-tsakanikas/fintelliguard"
}

variable "deploy_branches" {
  description = "Branches whose workflows may assume the deploy role. NOT a wildcard."
  type        = list(string)
  default     = ["main"]
}

variable "deploy_environments" {
  description = "GitHub environments whose jobs may assume the deploy role (matches deploy.yml)."
  type        = list(string)
  default     = ["dev"]
}

variable "create_oidc_provider" {
  description = <<-DESC
    Create the GitHub OIDC provider, or reference an existing one. An AWS account may have
    only ONE provider for a given URL, so set false if another stack already created it.
  DESC
  type        = bool
  default     = true
}
