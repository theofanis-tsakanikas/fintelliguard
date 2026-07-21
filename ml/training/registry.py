"""MLflow Model Registry registration + promotion, GUARDED by the promotion gate.

The gate logic lives in `promote.py` (pure, fully tested); here the registration
dependencies are injectable so the wiring can be unit-tested with mocks.

Unity Catalog does not have stages
----------------------------------
This module registered a model and then called `transition_model_version_stage`. That is the
CLASSIC workspace registry's API. Unity Catalog — which this project uses, and which every
three-level name like `fintelliguard.ml.fraud_scorer` implies — removed stages entirely and
replaced them with ALIASES; the call raises there. The mocks in the tests accepted it happily
because a mock accepts anything, so the wiring was "tested" and had never once run against a
registry of either kind.

So the mechanism is chosen from the NAME, which is the thing that actually determines it:
three dot-separated parts means Unity Catalog. The vocabulary stays the policy's own —
`production` and `staging` — rather than MLflow's champion/challenger convention, so the
governance docs and the registry agree on what a promoted model is called.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ml.training.fingerprint import FINGERPRINT_TAG, compute_fingerprint
from ml.training.promote import PromotionDecision
from ml.training.train import TrainConfig, TrainingResult

# The registry URI Unity Catalog requires. `mlflow.register_model` against a three-level name
# with the plain "databricks" registry writes to the WORKSPACE registry — which accepts the
# dotted string as a flat model name, so it appears to work and produces a model no serving
# endpoint can find.
_UC_REGISTRY_URI = "databricks-uc"


@dataclass(frozen=True)
class RegistrationResult:
    name: str
    version: str
    # Under Unity Catalog this holds the ALIAS; under the classic registry, the stage.
    # `mechanism` says which, so a caller never has to infer it from the string.
    stage: str
    mechanism: str = "stage"


def uses_unity_catalog(model_name: str) -> bool:
    """`catalog.schema.model` — the only naming shape Unity Catalog accepts."""
    return model_name.count(".") == 2


def target_stage(decision: PromotionDecision) -> str:
    """Map a promotion decision to a classic-registry stage."""
    return "Production" if decision.promote else "Staging"


def target_alias(decision: PromotionDecision) -> str:
    """Map a promotion decision to a Unity Catalog alias.

    Lowercase deliberately: UC aliases are case-sensitive and conventionally lowercase, and
    reusing the policy's own words keeps `docs/governance/` honest about what it describes.
    """
    return "production" if decision.promote else "staging"


def register_and_promote(
    result: TrainingResult,
    config: TrainConfig,
    decision: PromotionDecision,
    *,
    register_fn: Callable[[str, str], Any] | None = None,
    client: Any | None = None,
    model_uri: str | None = None,
    fingerprint: str | None = None,
) -> RegistrationResult:
    """Register a model and promote it per `decision`.

    A rejected model is still registered — it just never reaches production. Keeping the
    version means the metrics that rejected it are attached to something inspectable.

    `model_uri` defaults to the run's raw estimator, `runs:/<run>/model`. The training job
    overrides it with the PYFUNC logged by `ml.serving.endpoint.log_scoring_model`, because
    that — not the bare XGBClassifier — is what the serving endpoint must run: it wraps the
    estimator with the decision bands Bedrock's action group and the copilot both read. The
    two artefacts score identically and answer differently, so serving the wrong one produces
    a working endpoint with the wrong contract.
    """
    name = config.registered_model_name
    unity_catalog = uses_unity_catalog(name)

    if register_fn is None or client is None:
        import mlflow
        from mlflow.tracking import MlflowClient

        registry_uri = _UC_REGISTRY_URI if unity_catalog else config.tracking_uri
        if register_fn is None:
            mlflow.set_registry_uri(registry_uri)
            register_fn = mlflow.register_model
        client = client or MlflowClient(tracking_uri=config.tracking_uri, registry_uri=registry_uri)

    version = register_fn(model_uri or f"runs:/{result.run_id}/model", name)

    # Tag the version with the fingerprint of the code that produced it, so a later deploy can
    # tell whether an identical model already holds production and skip a needless retrain
    # (see `ml.training.reuse_decision`). Tagged for BOTH registries and BEFORE aliasing: the
    # reuse check reads the tag off whatever version the alias points at, so the tag must be
    # present the moment the alias is set. `fingerprint` is injectable for tests; by default it
    # is computed from the source on the box doing the registration.
    client.set_model_version_tag(
        name=name,
        version=version.version,
        key=FINGERPRINT_TAG,
        value=fingerprint if fingerprint is not None else compute_fingerprint(),
    )

    if unity_catalog:
        alias = target_alias(decision)
        client.set_registered_model_alias(name=name, alias=alias, version=version.version)
        return RegistrationResult(name, version.version, alias, mechanism="alias")

    stage = target_stage(decision)
    client.transition_model_version_stage(
        name=name,
        version=version.version,
        stage=stage,
        archive_existing_versions=decision.promote,
    )
    return RegistrationResult(name, version.version, stage, mechanism="stage")
