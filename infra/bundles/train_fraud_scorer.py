"""Train, gate and register the fraud scorer. Runs as a Databricks job.

Why this file exists
--------------------
Every piece of this was already written and unit-tested — `train_model`,
`evaluate_promotion`, `register_and_promote`, `log_scoring_model`,
`load_gold_training_features`. Nothing called them in sequence. There was no entry point,
so no model was ever trained, nothing was ever registered, and the main bundle's serving
endpoints failed on every deploy with

    Registered model 'fintelliguard.ml.fraud_scorer' does not exist.

Excellent components, no assembly — the same finding as the regulatory corpus, which had a
screening function and no path that invoked it.

The promotion gate is the point
-------------------------------
`evaluate_promotion` decides whether this model may be served, and its verdict is applied
whatever it says. A rejected model is still registered and still gets a version — it just
lands on the `staging` alias instead of `production`, so the metrics that rejected it stay
attached to something a reviewer can open. Registration is the record; the alias is the
permission. This job does NOT fail when the gate rejects: a red deploy would say "something
broke", and what happened is that a control worked.
"""

from __future__ import annotations

import pathlib
import sys


def _add_repo_root_to_path() -> None:
    """Put the synced repository root on `sys.path`.

    NOT via `__file__`: Databricks EXECUTES a `spark_python_task` rather than importing it,
    so `__file__` is undefined. Found by `ml/training` rather than by counting parents, so
    moving this file does not silently break it.
    """
    starts = []
    if sys.argv and sys.argv[0]:
        starts.append(pathlib.Path(sys.argv[0]).resolve().parent)
    starts.append(pathlib.Path.cwd().resolve())

    for start in starts:
        for candidate in (start, *start.parents):
            if (candidate / "ml" / "training").is_dir():
                sys.path.insert(0, str(candidate))
                return
    raise RuntimeError(
        f"cannot find the repo root (ml/training) from {starts} — check `sync.paths`"
    )


_add_repo_root_to_path()

from pyspark.sql import SparkSession  # noqa: E402

from ml.serving.endpoint import log_scoring_model  # noqa: E402
from ml.serving.scorer import ScoringConfig  # noqa: E402
from ml.training.dataset import LABEL_COLUMN, load_gold_training_features  # noqa: E402
from ml.training.promote import evaluate_promotion  # noqa: E402
from ml.training.registry import register_and_promote  # noqa: E402
from ml.training.train import TrainConfig, train_model  # noqa: E402

SOURCE_TABLE = "fintelliguard.gold.txn_features_training"
MODEL_NAME = "fintelliguard.ml.fraud_scorer"

# The Databricks-managed tracking + registry. Three-level model names require the Unity
# Catalog registry, which `register_and_promote` selects from the name.
TRACKING_URI = "databricks"
EXPERIMENT = "/Shared/fintelliguard-fraud-xgb"

# Below this the split is not a split. `train_model` stratifies a 70/15/15 train/val/test,
# so a few hundred rows leaves a test set too small for AUC to mean anything — and the gate
# would then be judging noise. Failing here says "the pipeline produced nothing usable",
# which is the actual problem, rather than reporting a confident metric from 40 rows.
MIN_ROWS = 5_000


def main() -> None:
    spark = SparkSession.builder.getOrCreate()

    # Already pandas — `load_gold_training_features` does the `.toPandas()` itself.
    frame = load_gold_training_features(spark, SOURCE_TABLE)
    print(f"loaded {len(frame):,} rows from {SOURCE_TABLE}")
    if len(frame) < MIN_ROWS:
        raise RuntimeError(
            f"{SOURCE_TABLE} has {len(frame):,} rows, below the {MIN_ROWS:,} needed for a "
            "meaningful train/val/test split — check that the DLT pipeline ran and that the "
            "IEEE-CIS data reached the raw bucket"
        )

    positives = int(frame[LABEL_COLUMN].sum()) if LABEL_COLUMN in frame else 0
    print(f"  label balance: {positives:,} fraud ({positives / len(frame):.2%})")

    config = TrainConfig(
        tracking_uri=TRACKING_URI,
        experiment=EXPERIMENT,
        registered_model_name=MODEL_NAME,
    )
    result = train_model(frame, config)
    print(f"trained run {result.run_id}: {result.metrics}")

    decision = evaluate_promotion(result.metrics)
    print(f"promotion gate: {'PASS' if decision.promote else 'REJECT'} — {decision.reason}")

    # The PYFUNC, not the bare estimator. `FraudScoringModel` wraps the XGBoost model with
    # the decision bands that Bedrock's action group and the copilot both depend on; serving
    # the raw estimator yields an endpoint that works and answers a different question.
    #
    # model_version is stamped with the MLflow run id, not left at ScoringConfig's
    # "fraud-xgb:local" default — that placeholder leaked into a real served verdict, which
    # then reported `model_version: fraud-xgb:local` while running the production model. The
    # run id is the honest identifier available HERE: the UC registered version is not known
    # until register_and_promote runs, one line below. Every score is now traceable to the
    # exact training run that produced it, which is the point of the field.
    model_uri = log_scoring_model(
        result.model, config=ScoringConfig(model_version=f"run:{result.run_id}")
    )
    registration = register_and_promote(result, config, decision, model_uri=model_uri)

    print(
        f"registered {registration.name} v{registration.version} "
        f"-> {registration.mechanism} '{registration.stage}'"
    )
    if not decision.promote:
        print(
            "NOT promoted to production. The serving endpoint will keep serving whatever "
            "version currently holds the production alias, which is the gate doing its job."
        )


if __name__ == "__main__":
    # No sys.exit — inside Databricks' notebook-like task host SystemExit propagates as an
    # exception and a clean exit is reported as INTERNAL_ERROR. See
    # tests/bundles/test_databricks_tasks.py.
    main()
