# Remote state in the bootstrap backend. Unique key isolates this layer's state from
# infra/aws and infra/bundles. Region literal must match the bootstrap backend.
terraform {
  backend "s3" {
    bucket         = "fintelliguard-tfstate"
    key            = "infra/databricks/terraform.tfstate"
    region         = "eu-central-1"
    dynamodb_table = "fintelliguard-tflock"
    encrypt        = true
  }
}
