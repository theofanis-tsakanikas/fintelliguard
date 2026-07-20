"""Thin DLT layer for gold — runs only on Databricks.

Wires the tested feature transforms into DLT tables. Import/lint-validated locally;
streaming/stateful execution is deferred to deploy.

The expectations sit on a gated VIEW, not on the clean table — see
`pipelines/silver/silver_pipeline.py` for the full reason. In short: `@dlt.expect_all` on a
frame that `select_valid` has already filtered can only ever report 100%, because every row
that could fail it has been removed before DLT looks.
"""

from __future__ import annotations

import dlt
from pyspark.sql import DataFrame


def _add_sync_root_to_path() -> None:
    """Put the directory ABOVE `pipelines/` on sys.path.

    DLT executes each source file like a notebook cell, not as a module inside a package,
    so `from . import gold_transforms` raised

        ImportError: attempted relative import with no known parent package

    on the first real pipeline run. `resources/pipelines.yml` predicted this — "the @dlt
    files use relative imports ... packaging the modules as a wheel is a deploy-phase
    refinement" — and a wheel is still the tidier long-term answer. This is the smaller one:
    the bundle already syncs the repository layout to the workspace, so the package is there
    and only needs to be reachable.

    `__file__` is checked but not relied on: it is undefined in some Databricks execution
    contexts (the seed job died on exactly that). cwd and sys.argv[0] are the fallbacks, and
    every candidate walks UP looking for the package rather than counting directories, so
    moving this file cannot silently break it.
    """
    import pathlib
    import sys

    candidates = []
    for value in (globals().get("__file__"), (sys.argv[0] if sys.argv else None)):
        if value:
            candidates.append(pathlib.Path(value).resolve().parent)
    candidates.append(pathlib.Path.cwd().resolve())

    for start in candidates:
        for directory in (start, *start.parents):
            if (directory / "pipelines" / "__init__.py").is_file():
                if str(directory) not in sys.path:
                    sys.path.insert(0, str(directory))
                return
    raise ImportError(
        "cannot locate the `pipelines` package from "
        f"{[str(c) for c in candidates]} — check `sync.paths` in infra/bundles/databricks.yml"
    )


_add_sync_root_to_path()

from pipelines.gold import gold_transforms  # noqa: E402


@dlt.view(
    name="gold.txn_features_realtime_gated",
    comment="Serving features, tagged pass/fail. The DQ metric is measured here.",
)
@dlt.expect_all(gold_transforms.GOLD_GATES)
def txn_features_realtime_gated() -> DataFrame:
    # Unfiltered: a row that fails a gate must still be here for the expectation to see it.
    features = gold_transforms.build_realtime_features(dlt.read("silver.transactions_clean"))
    return gold_transforms.gate_features(features)


@dlt.table(
    name="gold.txn_features_realtime",
    comment="The 15 serving features from the stream (validated).",
)
def txn_features_realtime() -> DataFrame:
    return gold_transforms.select_valid(dlt.read("gold.txn_features_realtime_gated"))


@dlt.table(
    name="gold.txn_features_realtime_quarantine",
    comment="Realtime feature rows failing a gate, kept for inspection.",
)
def txn_features_realtime_quarantine() -> DataFrame:
    return gold_transforms.select_quarantined(dlt.read("gold.txn_features_realtime_gated"))


@dlt.view(
    name="gold.txn_features_training_gated",
    comment="Training features, tagged pass/fail. The DQ metric is measured here.",
)
@dlt.expect_all(gold_transforms.GOLD_GATES)
def txn_features_training_gated() -> DataFrame:
    features = gold_transforms.build_training_features(dlt.read("silver.ieee_cis_clean"))
    return gold_transforms.gate_features(features)


@dlt.table(
    name="gold.txn_features_training",
    comment="The same 15 features + isFraud label, from IEEE-CIS (validated).",
)
def txn_features_training() -> DataFrame:
    return gold_transforms.select_valid(dlt.read("gold.txn_features_training_gated"))
