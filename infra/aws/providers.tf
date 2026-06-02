provider "aws" {
  region = var.aws_region

  # Applied to every taggable resource in this layer; resources add only a `Name`.
  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      Layer       = "aws"
      ManagedBy   = "terraform"
    }
  }
}
