# Secret PLACEHOLDERS only — KMS-encrypted containers, no values.
#
# Terraform creates the secret resources but NEVER their values (no
# aws_secretsmanager_secret_version here): keeping a value in TF would persist it in
# state. Values are injected out of band after apply, e.g.:
#
#   aws secretsmanager put-secret-value \
#     --secret-id fintelliguard/dev/databricks/token \
#     --secret-string "$DATABRICKS_TOKEN"
#
# or via CI (GitHub Actions OIDC → put-secret-value). Workloads (the Lambda
# action-group, copilot) fetch them at runtime via GetSecretValue; the Lambda's IAM
# scopes that read to the databricks/token secret ARN only (see iam.tf).

resource "aws_secretsmanager_secret" "this" {
  for_each = local.secret_paths

  name        = each.value
  description = "Placeholder for ${each.key} — value injected at runtime, never via Terraform."
  kms_key_id  = aws_kms_key.main.arn

  recovery_window_in_days = var.secret_recovery_window_days

  tags = { Name = each.value }
}
