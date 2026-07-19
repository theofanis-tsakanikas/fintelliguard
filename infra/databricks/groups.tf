# The analyst group the bundle grants against.
#
# `infra/bundles` grants SELECT on the gold schemas and the model registry to
# `var.analyst_group` (default `fintelliguard-analysts`), and the bundle deploy failed with
#
#     Could not find principal with name fintelliguard-analysts (PRINCIPAL_DOES_NOT_EXIST)
#
# because nothing created it. A grant to a principal that does not exist is not a
# least-privilege posture — it is a permission model that has never been applied, and it
# fails at the last step of the deploy rather than at review time.
#
# Created at ACCOUNT level and assigned to the workspace: Unity Catalog resolves grants
# against account-level identities, so a workspace-local group would satisfy the API and
# still not be the principal the catalog means.
resource "databricks_group" "analysts" {
  provider     = databricks.account
  display_name = var.analyst_group_name
}

resource "databricks_mws_permission_assignment" "analysts" {
  provider     = databricks.account
  workspace_id = databricks_mws_workspaces.this.workspace_id
  principal_id = databricks_group.analysts.id

  # USER, not ADMIN. Fraud analysts read the lakehouse through the Tier-3 copilot; they do
  # not administer the workspace.
  permissions = ["USER"]
}
