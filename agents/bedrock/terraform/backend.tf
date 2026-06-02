# Remote state in the bootstrap backend; unique key isolates this layer.
terraform {
  backend "s3" {
    bucket         = "fintelliguard-tfstate"
    key            = "agents/bedrock/terraform.tfstate"
    region         = "eu-central-1"
    dynamodb_table = "fintelliguard-tflock"
    encrypt        = true
  }
}
