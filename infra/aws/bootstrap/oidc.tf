# GitHub Actions OIDC — the identity every gated workflow assumes to reach AWS.
#
# It lives in bootstrap, and it must, for two reasons the workflows depend on:
#
# * It has to exist BEFORE any deploy: `deploy.yml`, `destroy.yml` and `bootstrap.yml` all
#   reference `secrets.AWS_DEPLOY_ROLE_ARN`, and there is no earlier layer to create it in.
# * It replaced long-lived IAM user keys — but the migration was half-done: the workflows
#   assumed a role that was defined NOWHERE in Terraform, so the single most privileged
#   identity in the system (it applies IAM, KMS and VPCs across three layers) was
#   unreviewable, and its trust condition — the thing that stops any fork assuming it — could
#   not be read. "IaC only, no console deployments" was violated by the one credential that
#   matters most.
#
# The trust policy is scoped to THIS repository and, by default, its protected branch and
# environment. A `sub` that is not pinned is the whole vulnerability: with `id-token: write`,
# any workflow in any fork or PR could otherwise assume this role.

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub's OIDC thumbprint. AWS now validates the JWT against the library of CAs, so this
  # is belt-and-braces, but the argument is required.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = { Name = "github-actions-oidc" }
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
  # repo:OWNER/REPO:ref:refs/heads/BRANCH and the deploy environments. NOT `repo:.../*`,
  # which would trust every branch and every PR.
  allowed_subs = concat(
    [for b in var.deploy_branches : "repo:${var.github_repository}:ref:refs/heads/${b}"],
    [for e in var.deploy_environments : "repo:${var.github_repository}:environment:${e}"],
  )
}

data "aws_iam_policy_document" "deploy_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # The pin. Only these exact subjects — this repo's protected branches and deploy
    # environments — may assume the role.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.allowed_subs
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "fintelliguard-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.deploy_assume.json
  max_session_duration = 3600

  tags = { Name = "fintelliguard-github-deploy" }
}

# The deploy role provisions the whole estate across three layers, so it is necessarily
# broad. `AdministratorAccess` is honest about that AND wrong to leave unbounded, so it is
# named here as the deliberate, reviewable decision it is — a narrower policy is the
# follow-up, and pretending a scoped policy exists when it does not would be the comment-
# denying-a-hole pattern this project keeps finding.
resource "aws_iam_role_policy_attachment" "deploy_admin" {
  # CKV_AWS_274: yes, this is AdministratorAccess, and that is the reviewable decision — not
  # a slip. The deploy role provisions IAM, KMS, VPCs and Bedrock across three layers; a
  # least-privilege policy for it is a real follow-up, and scoping it wrong would fail the
  # apply it exists to run. Skipped INLINE, on the resource, so the exception is attached to
  # the thing it excepts and a second admin attachment elsewhere is still flagged. What
  # bounds the blast radius today is the OIDC trust policy above: only this repo's protected
  # branches and deploy environments can assume it at all.
  #checkov:skip=CKV_AWS_274:Deploy role provisions the whole estate; least-privilege is a tracked follow-up, bounded by the sub-scoped OIDC trust.
  role       = aws_iam_role.deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
