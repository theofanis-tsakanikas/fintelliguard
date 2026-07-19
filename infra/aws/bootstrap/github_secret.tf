# The last hand-carried step, removed.
#
# `deploy.yml`, `destroy.yml` and `bootstrap.yml` all assume the role named by the
# `AWS_DEPLOY_ROLE_ARN` secret. That ARN is produced HERE, by the resource two files over —
# so having a human read it off the terminal and paste it into a GitHub form was never
# necessary, only traditional. Terraform knows the value and knows the repository; the
# owner/repo it writes to is `var.github_repository`, the same variable the OIDC trust
# policy pins `sub` to, so the secret cannot land anywhere the role would not be trusted.
#
# After this, establishing the whole chain of trust is one command:
#
#     terraform apply     ->  OIDC provider + deploy role + state backend + the secret
#
# and every workflow from then on authenticates with no static credential anywhere.
resource "github_actions_secret" "deploy_role_arn" {
  count = var.write_github_secret ? 1 : 0

  repository  = split("/", var.github_repository)[1]
  secret_name = "AWS_DEPLOY_ROLE_ARN"
  value       = aws_iam_role.deploy.arn
}
