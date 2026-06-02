"""Registry wiring — gate-guarded stage transition, tested with mocks (no live registry)."""

from __future__ import annotations

from unittest.mock import Mock

from ml.training.promote import PromotionDecision
from ml.training.registry import RegistrationResult, register_and_promote, target_stage
from ml.training.train import TrainConfig, TrainingResult


def _result():
    return TrainingResult(run_id="run123", metrics={}, feature_importance={}, model=None)


def test_target_stage_maps_decision():
    assert target_stage(PromotionDecision(True, "ok")) == "Production"
    assert target_stage(PromotionDecision(False, "bad")) == "Staging"


def test_register_and_promote_to_production():
    client = Mock()
    register_fn = Mock(return_value=Mock(version="4"))
    config = TrainConfig(registered_model_name="m")

    result = register_and_promote(
        _result(), config, PromotionDecision(True, "ok"), register_fn=register_fn, client=client
    )

    register_fn.assert_called_once_with("runs:/run123/model", "m")
    kwargs = client.transition_model_version_stage.call_args.kwargs
    assert kwargs["name"] == "m"
    assert kwargs["version"] == "4"
    assert kwargs["stage"] == "Production"
    assert result == RegistrationResult("m", "4", "Production")


def test_register_and_promote_rejected_goes_to_staging():
    client = Mock()
    register_fn = Mock(return_value=Mock(version="5"))
    config = TrainConfig(registered_model_name="m")

    result = register_and_promote(
        _result(),
        config,
        PromotionDecision(False, "auc too low"),
        register_fn=register_fn,
        client=client,
    )

    assert client.transition_model_version_stage.call_args.kwargs["stage"] == "Staging"
    assert result.stage == "Staging"
