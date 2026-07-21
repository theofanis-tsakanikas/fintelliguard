"""Registry wiring — gate-guarded stage transition, tested with mocks (no live registry)."""

from __future__ import annotations

from unittest.mock import Mock

from ml.training.fingerprint import FINGERPRINT_TAG
from ml.training.promote import PromotionDecision
from ml.training.registry import (
    RegistrationResult,
    register_and_promote,
    target_stage,
    uses_unity_catalog,
)
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


# --------------------------------------------------------------------------- #
# Unity Catalog — the registry this project actually uses
# --------------------------------------------------------------------------- #


def test_a_three_level_name_is_recognised_as_unity_catalog():
    assert uses_unity_catalog("fintelliguard.ml.fraud_scorer")
    assert not uses_unity_catalog("fintelliguard_fraud_xgb")
    # Two levels is the classic registry's "db.model", not UC.
    assert not uses_unity_catalog("ml.fraud_scorer")


def test_unity_catalog_promotion_sets_an_alias_and_never_a_stage():
    """UC removed stages; `transition_model_version_stage` RAISES there.

    This module only ever called that method, and the mocks accepted it — a Mock accepts any
    call — so the wiring passed its tests while being incapable of working against the
    registry this project uses. The assertion that matters is the NEGATIVE one.
    """
    client = Mock()
    register_fn = Mock(return_value=Mock(version="7"))
    config = TrainConfig(registered_model_name="fintelliguard.ml.fraud_scorer")

    result = register_and_promote(
        _result(), config, PromotionDecision(True, "ok"), register_fn=register_fn, client=client
    )

    client.transition_model_version_stage.assert_not_called()
    kwargs = client.set_registered_model_alias.call_args.kwargs
    assert kwargs == {
        "name": "fintelliguard.ml.fraud_scorer",
        "alias": "production",
        "version": "7",
    }
    assert result == RegistrationResult(
        "fintelliguard.ml.fraud_scorer", "7", "production", mechanism="alias"
    )


def test_a_rejected_model_gets_the_staging_alias_not_production():
    """The gate's whole purpose. A model that fails AUC/precision must not be servable."""
    client = Mock()
    register_fn = Mock(return_value=Mock(version="8"))
    config = TrainConfig(registered_model_name="fintelliguard.ml.fraud_scorer")

    result = register_and_promote(
        _result(),
        config,
        PromotionDecision(False, "auc_roc 0.71 < 0.83"),
        register_fn=register_fn,
        client=client,
    )

    assert client.set_registered_model_alias.call_args.kwargs["alias"] == "staging"
    assert result.stage == "staging"


def test_the_classic_registry_still_uses_stages():
    """The UC branch must not silently change behaviour for a non-UC name."""
    client = Mock()
    register_fn = Mock(return_value=Mock(version="9"))
    config = TrainConfig(registered_model_name="fintelliguard_fraud_xgb")

    register_and_promote(
        _result(), config, PromotionDecision(True, "ok"), register_fn=register_fn, client=client
    )

    client.set_registered_model_alias.assert_not_called()
    assert client.transition_model_version_stage.call_args.kwargs["stage"] == "Production"


# --------------------------------------------------------------------------- #
# The content fingerprint — what lets a later deploy skip a needless retrain
# --------------------------------------------------------------------------- #


def test_the_registered_version_is_tagged_with_the_content_fingerprint():
    """`reuse_decision` reads this tag off the aliased version; without it, every deploy would
    fail to confirm reuse and retrain. The fingerprint is injected here so the test does not
    depend on the repo's current source hash."""
    client = Mock()
    register_fn = Mock(return_value=Mock(version="7"))
    config = TrainConfig(registered_model_name="fintelliguard.ml.fraud_scorer")

    register_and_promote(
        _result(),
        config,
        PromotionDecision(True, "ok"),
        register_fn=register_fn,
        client=client,
        fingerprint="fp-abc123",
    )

    kwargs = client.set_model_version_tag.call_args.kwargs
    assert kwargs == {
        "name": "fintelliguard.ml.fraud_scorer",
        "version": "7",
        "key": FINGERPRINT_TAG,
        "value": "fp-abc123",
    }


def test_the_fingerprint_tag_is_set_before_the_alias_is_assigned():
    """The reuse check reads the tag off whatever version the alias points at, so the tag must
    exist the instant the alias is set — tagging after would leave a race where a deploy sees a
    promoted-but-untagged version and retrains."""
    calls = []
    client = Mock()
    client.set_model_version_tag.side_effect = lambda **_: calls.append("tag")
    client.set_registered_model_alias.side_effect = lambda **_: calls.append("alias")
    register_fn = Mock(return_value=Mock(version="7"))
    config = TrainConfig(registered_model_name="fintelliguard.ml.fraud_scorer")

    register_and_promote(
        _result(),
        config,
        PromotionDecision(True, "ok"),
        register_fn=register_fn,
        client=client,
        fingerprint="fp-abc123",
    )

    assert calls == ["tag", "alias"], f"tag must precede alias, got {calls}"


def test_a_rejected_model_is_also_fingerprinted():
    """A staging model can be promoted later by a deploy that finds its fingerprint current, so
    it must carry the tag too."""
    client = Mock()
    register_fn = Mock(return_value=Mock(version="8"))
    config = TrainConfig(registered_model_name="fintelliguard.ml.fraud_scorer")

    register_and_promote(
        _result(),
        config,
        PromotionDecision(False, "auc too low"),
        register_fn=register_fn,
        client=client,
        fingerprint="fp-xyz",
    )

    assert client.set_model_version_tag.call_args.kwargs["value"] == "fp-xyz"
