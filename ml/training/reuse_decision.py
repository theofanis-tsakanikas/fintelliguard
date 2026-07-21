"""Decide whether a model built from the CURRENT code already holds a registry alias.

The deploy workflow calls this to gate the IEEE fetch, the DLT run and the training job, so a
rebuild that would produce a byte-identical model does not pay for one.

Exit 0  -> a promoted model carries THIS code's content fingerprint; skip training and serve
           the existing version.
Exit 1  -> no such model (absent, untagged, or the code changed); training must run.

It fails to exit 1 (train) on ANY uncertainty. The two errors are not symmetric: a wrong
"skip" strands serving with a model that does not match the deployed features (or with none at
all), while a wrong "train" only costs a rebuild. So a missing model, a missing tag, a
fingerprint mismatch and a registry error all resolve the same way — train.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from ml.training.fingerprint import FINGERPRINT_TAG, compute_fingerprint


def production_fingerprint(model_name: str, alias: str) -> str:
    """The content fingerprint tagged on the version currently holding `alias`, or ""."""
    # Imported lazily: computing the local fingerprint needs no registry, and the unit tests
    # inject a fake fetch, so mlflow is only required on the deploy runner that actually queries.
    from mlflow.tracking import MlflowClient

    client = MlflowClient(registry_uri="databricks-uc")
    version = client.get_model_version_by_alias(model_name, alias)
    return (version.tags or {}).get(FINGERPRINT_TAG, "")


def decide(
    model_name: str,
    alias: str,
    *,
    fetch: Callable[[str, str], str] = production_fingerprint,
) -> bool:
    """True to REUSE the promoted model (skip training), False to train."""
    current = compute_fingerprint()
    try:
        existing = fetch(model_name, alias)
    except Exception as exc:  # noqa: BLE001 — any failure means "cannot confirm reuse" -> train
        print(f"no promoted model to reuse ({type(exc).__name__}): training will run")
        return False

    if existing and existing == current:
        print(f"production model matches this code (fingerprint {current[:12]}...): skipping")
        return True
    if existing:
        print(f"code changed since promoted model ({existing[:8]} != {current[:8]}): retraining")
    else:
        print("no promoted model, or it carries no fingerprint tag: training will run")
    return False


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m ml.training.reuse_decision <model_name> <alias>", file=sys.stderr)
        return 2
    return 0 if decide(argv[0], argv[1]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
