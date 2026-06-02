"""Genie space — DOCUMENTED FALLBACK.

Databricks Asset Bundles (CLI v1.1.0) has no `genie_space` resource type, so the Genie
space backing the copilot's `query_lakehouse` tool is created out-of-band via the
Databricks SDK/REST API. This is NOT part of `databricks bundle deploy`; run it manually
at deploy time:

    python infra/bundles/scripts/genie_space.py --warehouse-id <id> --catalog fintelliguard

Requires `databricks-sdk` and configured auth (DATABRICKS_HOST / DATABRICKS_TOKEN or a
profile). The Genie spaces API is in preview — confirm the path/version at deploy time.
Not executed during build/validate.
"""

from __future__ import annotations

import argparse

# Gold/silver tables the analyst queries via NL->SQL (precise facts for query_lakehouse).
GENIE_TABLES = [
    "gold.txn_features_realtime",
    "gold.resolved_cases",
    "silver.transactions_clean",
]


def create_genie_space(catalog: str, warehouse_id: str, name: str = "FintelliGuard Fraud Analyst"):
    """Create the Genie space over gold/silver via the Genie spaces API (preview)."""
    from databricks.sdk import WorkspaceClient

    workspace = WorkspaceClient()
    body = {
        "display_name": name,
        "description": "NL->SQL over gold/silver for fraud investigation (query_lakehouse).",
        "warehouse_id": warehouse_id,
        "table_identifiers": [f"{catalog}.{table}" for table in GENIE_TABLES],
        "instructions": (
            "Answer fraud-analyst questions with precise facts from the governed tables. "
            "Prefer exact counts, sums, and rates; cite the table you used."
        ),
    }
    return workspace.api_client.do("POST", "/api/2.0/genie/spaces", body=body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the FintelliGuard Genie space.")
    parser.add_argument("--catalog", default="fintelliguard")
    parser.add_argument("--warehouse-id", required=True)
    args = parser.parse_args()
    print(create_genie_space(args.catalog, args.warehouse_id))


if __name__ == "__main__":
    main()
