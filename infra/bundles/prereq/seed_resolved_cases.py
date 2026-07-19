"""Create and populate `gold.resolved_cases` — the DELTA_SYNC source for the case index.

Runs as a Databricks job BEFORE the main bundle deploys, because the vector index cannot be
created against a table that does not exist and DAB cannot express "run this job, then build
that index" inside one deploy.

The rows are the deterministic SYNTHETIC fixture from `agents/databricks/cases/seed.py`;
every one of them declares itself synthetic in three places, the load-bearing one being that
the disclosure opens `case_text` — the column Vector Search embeds and hands back to an
analyst. See that module for why the marker is permanent rather than transitional.
"""

from __future__ import annotations

import pathlib
import sys

from pyspark.sql import SparkSession

# `sync.paths` lists this directory AND ../../../agents, so the sync root is the repository
# root and the layout in the workspace mirrors the repo. Three levels up from this file is
# therefore the importable root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from agents.databricks.cases import build_seed_cases, resolved_cases_schema  # noqa: E402

CATALOG = "fintelliguard"
SCHEMA = "gold"
TABLE = "resolved_cases"


def main() -> int:
    spark = SparkSession.builder.getOrCreate()
    fqn = f"{CATALOG}.{SCHEMA}.{TABLE}"

    cases = build_seed_cases()
    rows = [c.as_row() for c in cases]
    frame = spark.createDataFrame(rows, schema=resolved_cases_schema())

    # Overwrite, not append: re-running the seed must not multiply the fixture.
    frame.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(fqn)

    count = spark.table(fqn).count()
    print(f"seeded {fqn} with {count} synthetic resolved cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
