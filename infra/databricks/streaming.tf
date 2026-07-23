# =============================================================================
# Registers the MSK instance profile with Databricks so a classic streaming cluster
# can authenticate to the brokers with IAM. Two things are required and both live
# here, next to the cross-account role they extend:
#
#   1. The cross-account role Databricks assumes must be allowed to iam:PassRole the
#      MSK role onto the cluster's EC2 instances — without it, launching a cluster
#      WITH the instance profile is denied ("not authorized to perform: iam:PassRole").
#   2. The instance profile must be registered as a databricks_instance_profile before
#      any cluster can reference it.
#
# The instance profile itself (and its role's kafka-cluster grants) are created in
# infra/aws; this layer only grants the pass and registers the profile. All of it is
# free — it mints no brokers. The MSK cluster is still gated by enable_msk in infra/aws.
# =============================================================================

# (1) Let the Databricks cross-account role pass the MSK role to cluster instances.
# Separate inline policy — additive to the managed cross-account policy, never edits it.
data "aws_iam_policy_document" "cross_account_pass_msk" {
  statement {
    sid       = "PassMskAccessRoleToClusters"
    actions   = ["iam:PassRole"]
    resources = [local.aws.msk_access_role_arn]
  }
}

resource "aws_iam_role_policy" "cross_account_pass_msk" {
  name   = "databricks-pass-msk-access"
  role   = aws_iam_role.cross_account.id
  policy = data.aws_iam_policy_document.cross_account_pass_msk.json
}

# (2) Register the instance profile in the workspace. skip_validation because the
# PassRole grant above and the profile in infra/aws may still be settling when this
# runs (IAM is eventually consistent); the streaming cluster is the real proof it works.
resource "databricks_instance_profile" "msk_access" {
  provider             = databricks.workspace
  instance_profile_arn = local.aws.msk_access_instance_profile_arn
  skip_validation      = true

  depends_on = [aws_iam_role_policy.cross_account_pass_msk]
}
