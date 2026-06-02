variable "aws_region" {
  description = "AWS region for the Terraform state backend resources."
  type        = string
  default     = "us-east-1"
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
