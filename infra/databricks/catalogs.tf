# Project catalog + medallion schemas, per CLAUDE.md naming:
#   fintelliguard.{bronze,silver,gold}
# Created through the WORKSPACE provider, after the metastore is assigned.

resource "databricks_catalog" "fintelliguard" {
  provider = databricks.workspace
  name     = var.project
  comment  = "FintelliGuard lakehouse — medallion catalog."

  force_destroy = true

  depends_on = [databricks_metastore_assignment.this]
}

resource "databricks_schema" "medallion" {
  provider = databricks.workspace
  for_each = toset(var.schemas)

  catalog_name = databricks_catalog.fintelliguard.name
  name         = each.key
  comment      = "Medallion ${each.key} layer."

  force_destroy = true
}
