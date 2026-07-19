# Project catalog + medallion schemas, per CLAUDE.md naming:
#   fintelliguard.{bronze,silver,gold}
# Created through the WORKSPACE provider, after the metastore is assigned.

resource "databricks_catalog" "fintelliguard" {
  provider = databricks.workspace
  name     = var.project
  comment  = "FintelliGuard lakehouse — medallion catalog."

  # Explicit managed-table root. With no `storage_root` the catalog falls back to the
  # METASTORE's, which this metastore deliberately does not have — that is what failed
  # deploy run 5 ("Metastore storage root URL does not exist"). See uc_storage.tf.
  storage_root = databricks_external_location.uc.url

  force_destroy = true

  depends_on = [databricks_metastore_assignment.this, databricks_external_location.uc]
}

resource "databricks_schema" "medallion" {
  provider = databricks.workspace
  for_each = toset(var.schemas)

  catalog_name = databricks_catalog.fintelliguard.name
  name         = each.key
  comment      = "Medallion ${each.key} layer."

  force_destroy = true
}
