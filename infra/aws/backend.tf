# Remote state in the bootstrap-created backend (infra/aws/bootstrap).
#
# Backend config cannot use variables, so bucket/region/table are literals — they MUST
# match the bootstrap outputs (state_bucket / region / lock_table). The `key` is unique
# to THIS layer, keeping its state isolated from infra/databricks and infra/bundles.
terraform {
  backend "s3" {
    bucket         = "fintelliguard-tfstate"
    key            = "infra/aws/terraform.tfstate"
    region         = "eu-central-1"
    dynamodb_table = "fintelliguard-tflock"
    encrypt        = true
  }
}
