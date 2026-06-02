# infra/aws/ — Terraform layer 1

AWS edge infrastructure: **MSK** (Kafka), **S3** (raw + curated zones), **Secrets
Manager**, **API Gateway**, **IAM**, **KMS**.

- Remote state stored in the S3 bucket + DynamoDB lock table created by `bootstrap/`.
- Exposes outputs (VPC IDs, bucket names, MSK brokers) consumed by other layers via
  `terraform_remote_state` data sources — never by reaching into this layer's state.
- Secrets are created here but **values are never committed**; they are injected out of
  band and read by workloads at runtime.

`bootstrap/` is a standalone, one-time config (local state) — see its README.
