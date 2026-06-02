terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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
