"""FraudScoring handler: envelope, contract fidelity, error path, missing params."""

from __future__ import annotations

import json

import handler
from errors import FraudScoreError

from ml.serving.scorer import CONTRACT_KEYS, FraudScorer


class _ScorerBackedMosaic:
    """A FraudScoreService that runs the REAL ml/serving scorer (mocks the endpoint)."""

    def __init__(self, model):
        self._scorer = FraudScorer(model)

    def get_fraud_score(self, features):
        return self._scorer.score(features)


class _FakeFeatureStore:
    def __init__(self, features):
        self._features = features

    def fetch(self, *, card_hash, transaction_id):
        return dict(self._features)


def _event(params=None):
    if params is None:
        params = [
            {"name": "transaction_id", "value": "t-1"},
            {"name": "card_hash", "value": "abc123"},
        ]
    return {
        "messageVersion": "1.0",
        "actionGroup": "FraudScoring",
        "function": "get_fraud_score",
        "parameters": params,
    }


def test_handler_returns_contract_in_response_envelope(trained_xgb, sample_features):
    deps = handler.Dependencies(
        _FakeFeatureStore(sample_features), _ScorerBackedMosaic(trained_xgb)
    )
    response = handler.lambda_handler(_event(), dependencies=deps)

    assert response["messageVersion"] == "1.0"
    inner = response["response"]
    assert inner["actionGroup"] == "FraudScoring"
    assert inner["function"] == "get_fraud_score"
    assert "responseState" not in inner["functionResponse"]  # success

    body = json.loads(inner["functionResponse"]["responseBody"]["TEXT"]["body"])
    # Contract fidelity: exactly the bedrock-integration.md keys, equal to a direct score.
    assert tuple(body.keys()) == CONTRACT_KEYS
    assert body == FraudScorer(trained_xgb).score(sample_features)
    assert body["top_features"]


def test_handler_error_path_returns_failure_envelope(sample_features):
    class _BrokenMosaic:
        def get_fraud_score(self, features):
            raise FraudScoreError("endpoint unavailable")

    deps = handler.Dependencies(_FakeFeatureStore(sample_features), _BrokenMosaic())
    response = handler.lambda_handler(_event(), dependencies=deps)

    function_response = response["response"]["functionResponse"]
    assert function_response["responseState"] == "FAILURE"
    body = json.loads(function_response["responseBody"]["TEXT"]["body"])
    assert "endpoint unavailable" in body["error"]


def test_handler_missing_parameter_is_graceful(trained_xgb, sample_features):
    deps = handler.Dependencies(
        _FakeFeatureStore(sample_features), _ScorerBackedMosaic(trained_xgb)
    )
    event = _event(params=[{"name": "transaction_id", "value": "t-1"}])  # no card_hash

    response = handler.lambda_handler(event, dependencies=deps)
    function_response = response["response"]["functionResponse"]
    assert function_response["responseState"] == "FAILURE"
    assert "card_hash" in json.loads(function_response["responseBody"]["TEXT"]["body"])["error"]
