terraform {
  required_version = ">= 1.5"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.50"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # For the IAM propagation delay before Databricks validates the cross-account role —
    # see the comment on `time_sleep.iam_propagation` in workspace.tf.
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }
}
