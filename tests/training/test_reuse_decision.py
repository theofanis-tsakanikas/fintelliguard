"""The train-or-reuse decision. The asymmetry is the whole design: a wrong 'skip' strands
serving with the wrong (or no) model, a wrong 'train' only costs a rebuild — so every
uncertain case must resolve to 'train'.
"""

from __future__ import annotations

import pytest

from ml.training import reuse_decision as rd


@pytest.fixture
def fixed_fingerprint(monkeypatch):
    """Pin the local fingerprint so tests control both sides of the comparison."""
    monkeypatch.setattr(rd, "compute_fingerprint", lambda: "abc123")
    return "abc123"


def test_reuse_when_promoted_model_matches_the_current_code(fixed_fingerprint):
    reuse = rd.decide("m", "production", fetch=lambda n, a: fixed_fingerprint)
    assert reuse is True


def test_train_when_the_promoted_fingerprint_differs(fixed_fingerprint):
    reuse = rd.decide("m", "production", fetch=lambda n, a: "deadbeef")
    assert reuse is False, "code changed since the promoted model but the decision reused it"


def test_train_when_there_is_no_promoted_fingerprint(fixed_fingerprint):
    # Empty string = model present but untagged, or no model — either way, cannot confirm reuse.
    reuse = rd.decide("m", "production", fetch=lambda n, a: "")
    assert reuse is False


def test_train_when_the_registry_lookup_raises(fixed_fingerprint):
    """A transient registry error must fail CLOSED to training, never to a silent skip."""

    def boom(name, alias):
        raise RuntimeError("registry unreachable")

    reuse = rd.decide("m", "production", fetch=boom)
    assert reuse is False


def test_main_exit_codes_map_reuse_to_zero_and_train_to_one(monkeypatch):
    monkeypatch.setattr(rd, "decide", lambda name, alias: True)
    assert rd.main(["m", "production"]) == 0
    monkeypatch.setattr(rd, "decide", lambda name, alias: False)
    assert rd.main(["m", "production"]) == 1


def test_main_rejects_wrong_arity():
    assert rd.main([]) == 2
    assert rd.main(["only-one"]) == 2


def test_production_fingerprint_reads_the_tag_off_the_aliased_version(monkeypatch):
    """The fetch reads the fingerprint tag from whatever version holds the alias."""
    captured = {}

    class FakeVersion:
        tags = {rd.FINGERPRINT_TAG: "tagged-value", "other": "x"}

    class FakeClient:
        def __init__(self, registry_uri=None):
            captured["registry_uri"] = registry_uri

        def get_model_version_by_alias(self, name, alias):
            captured["lookup"] = (name, alias)
            return FakeVersion()

    import mlflow.tracking

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    value = rd.production_fingerprint("fintelliguard.ml.fraud_scorer", "production")

    assert value == "tagged-value"
    assert captured["registry_uri"] == "databricks-uc", "must query the Unity Catalog registry"
    assert captured["lookup"] == ("fintelliguard.ml.fraud_scorer", "production")


def test_production_fingerprint_is_empty_when_the_tag_is_absent(monkeypatch):
    class FakeVersion:
        tags = {"unrelated": "x"}

    class FakeClient:
        def __init__(self, registry_uri=None):
            pass

        def get_model_version_by_alias(self, name, alias):
            return FakeVersion()

    import mlflow.tracking

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    assert rd.production_fingerprint("m", "production") == ""
