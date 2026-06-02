"""MLflow Model Registry registration + stage transition, GUARDED by the promotion gate.

Registry operations are cloud/backed-by-a-database, so they are not exercised against a
live registry in tests. The gate logic lives in `promote.py` (pure, fully tested); here
the registration/transition dependencies are injectable so the wiring can be unit-tested
with mocks. `target_stage` is pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ml.training.promote import PromotionDecision
from ml.training.train import TrainConfig, TrainingResult


@dataclass(frozen=True)
class RegistrationResult:
    name: str
    version: str
    stage: str


def target_stage(decision: PromotionDecision) -> str:
    """Map a promotion decision to a registry stage."""
    return "Production" if decision.promote else "Staging"


def register_and_promote(
    result: TrainingResult,
    config: TrainConfig,
    decision: PromotionDecision,
    *,
    register_fn: Callable[[str, str], Any] | None = None,
    client: Any | None = None,
) -> RegistrationResult:
    """Register the run's model and transition its stage per `decision`.

    `register_fn` / `client` default to the live MLflow registry but are injectable for
    tests. A rejected model is registered into Staging (never Production).
    """
    if register_fn is None or client is None:
        import mlflow
        from mlflow.tracking import MlflowClient

        register_fn = register_fn or mlflow.register_model
        client = client or MlflowClient(
            tracking_uri=config.tracking_uri, registry_uri=config.tracking_uri
        )

    model_uri = f"runs:/{result.run_id}/model"
    version = register_fn(model_uri, config.registered_model_name)
    stage = target_stage(decision)
    client.transition_model_version_stage(
        name=config.registered_model_name,
        version=version.version,
        stage=stage,
        archive_existing_versions=decision.promote,
    )
    return RegistrationResult(
        name=config.registered_model_name, version=version.version, stage=stage
    )
