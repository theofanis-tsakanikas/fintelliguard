# ---- Identity / region -------------------------------------------------------
variable "aws_region" {
  description = "AWS region for this layer's resources. Independent of the state backend region (see backend.tf)."
  type        = string
  default     = "eu-central-1"
}

variable "project" {
  description = "Project name, used as a resource name prefix and tag."
  type        = string
  default     = "fintelliguard"
}

variable "environment" {
  description = "Deployment environment (dev/stg/prod). Part of resource names and tags."
  type        = string
  default     = "dev"
}

variable "tags" {
  description = "Extra tags merged on top of the provider default_tags."
  type        = map(string)
  default     = {}
}

# ---- Networking --------------------------------------------------------------
variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to span (also the MSK broker count). Subnet CIDR lists must have at least this many entries."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2
    error_message = "az_count must be at least 2 for multi-AZ subnets and MSK."
  }
}

variable "public_subnet_cidrs" {
  description = "CIDRs for the public subnets, one per AZ."
  type        = list(string)
  default     = ["10.20.0.0/24", "10.20.1.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDRs for the private subnets, one per AZ."
  type        = list(string)
  default     = ["10.20.16.0/20", "10.20.32.0/20"]
}

variable "databricks_privatelink_service_name" {
  description = "VPC endpoint service name of the Databricks PrivateLink endpoint used to reach Mosaic Model Serving privately. Empty = endpoint not created yet (wired up once the Databricks layer exists)."
  type        = string
  default     = ""
}

# ---- S3 ----------------------------------------------------------------------
variable "raw_bucket_name" {
  description = "Override name for the raw landing bucket. Empty = auto-name `<project>-raw-<account_id>` (globally unique)."
  type        = string
  default     = ""
}

variable "raw_retention_days" {
  description = "Days after which raw objects expire (lifecycle). Controls storage cost."
  type        = number
  default     = 365
}

variable "raw_noncurrent_retention_days" {
  description = "Days to keep noncurrent (overwritten) object versions before expiring them."
  type        = number
  default     = 30
}

# ---- Secrets -----------------------------------------------------------------
variable "managed_secret_names" {
  description = "Secrets Manager secret names to create as EMPTY placeholders (values injected out of band — never in Terraform). Paths are prefixed with `<project>/<environment>/`."
  type        = list(string)
  default     = ["databricks/token", "langsmith/api-key"]
}

variable "secret_recovery_window_days" {
  description = "Recovery window before a deleted secret is permanently removed."
  type        = number
  default     = 7
}

# ---- MSK (cost-guarded) ------------------------------------------------------
variable "enable_msk" {
  description = "Provision the MSK cluster. DEFAULT false: dev uses local Kafka (Docker). Set true ONLY for integration testing / the final demo — MSK is the most expensive resource in this layer."
  type        = bool
  default     = false
}

variable "msk_kafka_version" {
  description = "Kafka version for the MSK cluster."
  type        = string
  default     = "3.6.0"
}

variable "msk_broker_instance_type" {
  description = "MSK broker instance type. Smallest viable for dev/integration."
  type        = string
  default     = "kafka.t3.small"
}

variable "msk_broker_ebs_gb" {
  description = "EBS volume size (GB) per MSK broker."
  type        = number
  default     = 10
}

variable "flow_log_retention_days" {
  description = "Retention for VPC flow logs. 90 days covers a typical audit lookback."
  type        = number
  default     = 365
}
