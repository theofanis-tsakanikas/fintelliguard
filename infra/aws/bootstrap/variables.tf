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

variable "write_github_secret" {
  description = <<-DESC
    Write the deploy role's ARN into the repository's `AWS_DEPLOY_ROLE_ARN` Actions secret.

    This is what removes the last hand-carried step from the trust anchor. The chicken-and-
    egg is real and cannot be designed away — OIDC needs a role, creating the role needs
    credentials, and OIDC exists to remove credentials — so a human must run THIS layer
    once, locally, with their own admin credentials. That part is correct and universal.

    What is not inherent is the copy-paste that followed: read an ARN out of the terraform
    output, paste it into a GitHub form. A value transcribed by hand is a value that can be
    transcribed wrong, into the wrong repository, or silently not at all — and the failure
    surfaces much later as an AssumeRole error in CI. Terraform already knows the ARN and
    already knows the repository; let it write it.

    Set false if the runner has no GitHub token (the provider needs `GITHUB_TOKEN`, or
    `gh auth token`), or if repository secrets are managed by something else.
  DESC
  type        = bool
  default     = true
}
