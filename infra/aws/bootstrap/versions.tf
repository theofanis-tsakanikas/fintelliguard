terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # Writes the deploy role's ARN straight into the repository's Actions secret, so the
    # trust anchor is established by ONE command with nothing copied by hand. Optional:
    # see `write_github_secret` in variables.tf.
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }

  # Bootstrap intentionally uses LOCAL state: it is the chicken-and-egg config that
  # CREATES the remote backend (S3 bucket + DynamoDB lock table) every other layer
  # then consumes. Do not migrate this layer to the remote backend.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "fintelliguard"
      Layer     = "bootstrap"
      ManagedBy = "terraform"
    }
  }
}

# Owner is derived from `github_repository` ("owner/repo") — the same variable the OIDC
# trust policy pins `sub` to, so the secret can never be written to a repository other than
# the one the role actually trusts.
#
# The token comes from the environment (`GITHUB_TOKEN`, or `gh auth token`) and is never
# stored in state: this provider holds no resources whose attributes echo it back.
provider "github" {
  owner = split("/", var.github_repository)[0]
}
