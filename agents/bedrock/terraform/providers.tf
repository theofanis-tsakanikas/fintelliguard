provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      Layer       = "agents-bedrock"
      ManagedBy   = "terraform"
    }
  }
}
