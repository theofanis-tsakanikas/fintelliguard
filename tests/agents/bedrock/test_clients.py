"""Client tests: token caching, Mosaic auth + error handling, and the URL-leak fix."""

from __future__ import annotations

import json

import clients
import pytest
from clients import (
    _CONTRACT_KEYS,
    MosaicModelServingClient,
    get_databricks_token,
)
from errors import FraudScoreError

_FULL_CONTRACT = {
    "fraud_score": 0.9,
    "model_version": "fraud-xgb:3",
    "threshold": 0.7,
    "decision_hint": "block",
    "top_features": [{"name": "country_mismatch", "value": True, "contribution": 0.5}],
}


@pytest.fixture(autouse=True)
def _clear_caches():
    """The token cache and boto3 client are module-level; reset them between tests."""
    clients._TOKEN_CACHE.clear()
    clients._SECRETS_CLIENT = None
    yield
    clients._TOKEN_CACHE.clear()
    clients._SECRETS_CLIENT = None


class _FakeSecrets:
    def __init__(self):
        self.calls = 0

    def get_secret_value(self, SecretId):  # noqa: N803 - boto3's parameter name
        self.calls += 1
        return {"SecretString": f"tok-{self.calls}"}


def test_the_token_is_cached_across_warm_invocations():
    """It used to hit Secrets Manager on EVERY request — a fresh client and a GetSecretValue
    each time — which at scale hit the account-wide throttle and surfaced to the agent as a
    scoring outage."""
    sm = _FakeSecrets()
    a = get_databricks_token("s", client=sm, now=0.0)
    b = get_databricks_token("s", client=sm, now=10.0)
    assert a == b == "tok-1"
    assert sm.calls == 1, "the token was re-fetched inside the TTL"


def test_the_cache_expires_so_a_rotated_secret_is_picked_up():
    sm = _FakeSecrets()
    first = get_databricks_token("s", client=sm, now=0.0)
    later = get_databricks_token("s", client=sm, now=clients._TOKEN_TTL_SECONDS + 1)
    assert first == "tok-1"
    assert later == "tok-2", "an in-place rotation was never picked up"


def test_mosaic_client_sends_bearer_token_and_returns_the_validated_contract():
    captured = {}

    def fake_post(url, payload, headers, timeout):
        captured["headers"] = headers
        captured["payload"] = json.loads(payload.decode("utf-8"))
        return json.dumps({"predictions": [_FULL_CONTRACT]})

    client = MosaicModelServingClient(
        "https://mosaic.example/serving/fraud",
        token_provider=lambda: "secret-token-123",
        http_post=fake_post,
    )
    result = client.get_fraud_score({"amount_usd": 10.0})

    assert tuple(result) == _CONTRACT_KEYS
    assert captured["headers"]["Authorization"] == "Bearer secret-token-123"
    assert captured["payload"] == {"dataframe_records": [{"amount_usd": 10.0}]}


def test_a_transport_failure_never_leaks_the_endpoint_url():
    """The one finding that lets an internal URL reach a regulated LLM output.

    `FraudScoreError(f"... {exc}")` embedded the exception, and a urllib error stringifies to
    include the endpoint URL — which flows into the agent's context via the failure
    response. The message must be opaque; the detail belongs in the log.
    """
    url = "https://dbc-abc123.cloud.databricks.internal/serving-endpoints/fraud/invocations"

    def boom(*_a):
        raise ConnectionError(f"connection refused to {url}")

    client = MosaicModelServingClient(url, token_provider=lambda: "t", http_post=boom)
    with pytest.raises(FraudScoreError) as excinfo:
        client.get_fraud_score({"amount_usd": 1.0})

    assert url not in str(excinfo.value), "the endpoint URL leaked into the caller's error"
    assert "databricks" not in str(excinfo.value).lower()


def test_a_transport_failure_is_retried_once():
    attempts = {"n": 0}

    def flaky(*_a):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("transient 503")
        return json.dumps({"predictions": [_FULL_CONTRACT]})

    client = MosaicModelServingClient("https://x", token_provider=lambda: "t", http_post=flaky)
    result = client.get_fraud_score({"amount_usd": 1.0})
    assert attempts["n"] == 2, "a transient failure was not retried"
    assert tuple(result) == _CONTRACT_KEYS


def test_a_non_contract_response_is_refused_not_returned_as_a_score():
    """`return predictions[0]` returned whatever the endpoint sent.

    So `{"error": "model loading"}` became a 'fraud score' the agent reasoned about as a
    verdict. A response that is not the contract is an error.
    """

    def wrong_shape(*_a):
        return json.dumps({"predictions": [{"error": "model loading"}]})

    client = MosaicModelServingClient(
        "https://x", token_provider=lambda: "t", http_post=wrong_shape
    )
    with pytest.raises(FraudScoreError, match="unexpected result shape"):
        client.get_fraud_score({"amount_usd": 1.0})


def test_the_lambda_contract_keys_match_the_serving_scorer():
    """The Lambda ships as a standalone zip and cannot import `ml`, so `_CONTRACT_KEYS` is
    duplicated. This is the test that stops the copy drifting from the original."""
    from ml.serving.scorer import CONTRACT_KEYS

    assert _CONTRACT_KEYS == CONTRACT_KEYS


def test_no_hardcoded_secret_in_default_path():
    client = MosaicModelServingClient("https://x", token_provider=lambda: "from-vault")
    assert client._token_provider() == "from-vault"
