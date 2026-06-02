# Unity Catalog metastore (account-level) + assignment to the workspace. One metastore
# per region; the workspace is bound to it before any catalog/schema can be created.

resource "databricks_metastore" "this" {
  provider = databricks.account
  name     = local.metastore_name
  region   = var.aws_region

  # Optional metastore-level managed storage; omitted unless an S3 URI is provided
  # (modern UC favours catalog-level storage).
  storage_root = var.metastore_storage_root != "" ? var.metastore_storage_root : null

  force_destroy = true
}

resource "databricks_metastore_assignment" "this" {
  provider             = databricks.account
  metastore_id         = databricks_metastore.this.id
  workspace_id         = databricks_mws_workspaces.this.workspace_id
  default_catalog_name = var.project
}
