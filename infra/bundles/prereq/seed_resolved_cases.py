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


def _add_repo_root_to_path() -> None:
    """Put the synced repository root on `sys.path`.

    NOT via `__file__`: Databricks EXECUTES a `spark_python_task` rather than importing it,
    so `__file__` is undefined and the first version of this died on `NameError` after the
    cluster had already spun up — three minutes to learn a one-line fact.

    `sync.paths` lists this directory and ../../../agents, so the sync root is the repo root
    and the workspace layout mirrors it. Rather than counting parents (which breaks the
    moment this file moves), find the root by looking for the package that must be there.
    """
    starts = []
    if sys.argv and sys.argv[0]:
        starts.append(pathlib.Path(sys.argv[0]).resolve().parent)
    starts.append(pathlib.Path.cwd().resolve())

    for start in starts:
        for candidate in (start, *start.parents):
            if (candidate / "agents" / "databricks" / "cases").is_dir():
                sys.path.insert(0, str(candidate))
                return
    raise RuntimeError(
        f"cannot find the repo root (agents/databricks/cases) from {starts} — "
        "check `sync.paths` in this bundle"
    )


_add_repo_root_to_path()

from agents.databricks.cases import build_seed_cases, resolved_cases_schema  # noqa: E402

CATALOG = "fintelliguard"
SCHEMA = "gold"
TABLE = "resolved_cases"


def main() -> None:
    spark = SparkSession.builder.getOrCreate()
    fqn = f"{CATALOG}.{SCHEMA}.{TABLE}"

    cases = build_seed_cases()
    rows = [c.as_row() for c in cases]
    frame = spark.createDataFrame(rows, schema=resolved_cases_schema())

    # Overwrite, not append: re-running the seed must not multiply the fixture.
    frame.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(fqn)

    # A DELTA_SYNC vector index keeps itself current by READING this table's change data
    # feed, and Databricks refuses to build one without it:
    #
    #     Source table fintelliguard.gold.resolved_cases is not a valid Vector Search
    #     source. Please retry after enabling change data feed
    #     (delta.enableChangeDataFeed = true).
    #
    # Set here rather than in the index definition because it is a property of the TABLE, and
    # this job is what creates the table. ALTER rather than a writer option: the option only
    # applies when the table is created, so an overwrite of an existing table would leave a
    # pre-CDF table silently unchanged. ALTER is idempotent and applies either way.
    spark.sql(f"ALTER TABLE {fqn} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

    count = spark.table(fqn).count()
    print(f"seeded {fqn} with {count} synthetic resolved cases")


if __name__ == "__main__":
    # NO `sys.exit(...)`, not even with 0.
    #
    # Databricks runs a `spark_python_task` inside a notebook-like host rather than as a
    # standalone process, and there `SystemExit` is an EXCEPTION that propagates — it does
    # not terminate anything. So a clean `sys.exit(0)` is reported as:
    #
    #     SystemExit: 0
    #     Task seed failed with message: Workload failed
    #     Error: failed to reach TERMINATED or SKIPPED, got INTERNAL_ERROR
    #
    # A job that did its work perfectly, failing on the way out. That is what deploy run
    # 29707464142 hit, 11 minutes in, after the table had already been written.
    #
    # Returning normally is the success signal; an exception is the failure signal. There is
    # no third channel here, so an exit code is not merely unnecessary, it is wrong.
    main()
