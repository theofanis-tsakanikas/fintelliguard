"""Client tests: token caching, Mosaic auth + error handling, and the URL-leak fix."""

from __future__ import annotations

import json

import clients
import pytest
from clients import (
    _CONTRACT_KEYS,
    MosaicModelServingClient,
    S3OnlineFeatureStore,
    get_databricks_oauth_token,
    get_databricks_token,
)
from errors import FeatureStoreError, FraudScoreError

_FULL_CONTRACT = {
    "fraud_score": 0.9,
    "model_version": "fraud-xgb:3",
    "threshold": 0.7,
    "decision_hint": "block",
    "top_features": [{"name": "country_mismatch", "value": True, "contribution": 0.5}],
}


@pytest.fixture(autouse=True)
def _clear_caches():
    """The token/feature caches and boto3 clients are module-level; reset them between tests."""
    clients._TOKEN_CACHE.clear()
    clients._FEATURES_CACHE.clear()
    clients._OAUTH_CACHE.clear()
    clients._SECRETS_CLIENT = None
    clients._S3_CLIENT = None
    yield
    clients._TOKEN_CACHE.clear()
    clients._FEATURES_CACHE.clear()
    clients._OAUTH_CACHE.clear()
    clients._SECRETS_CLIENT = None
    clients._S3_CLIENT = None


class _OAuthSecrets:
    """Secrets client returning the SP credential JSON the OAuth exchange reads."""

    def __init__(self, token_endpoint="https://dbc-x.cloud.databricks.com/oidc/v1/token"):
        self.creds = {
            "client_id": "cid",
            "client_secret": "csecret",
            "token_endpoint": token_endpoint,
        }

    def get_secret_value(self, SecretId):  # noqa: N803 - boto3's parameter name
        return {"SecretString": json.dumps(self.creds)}


def test_oauth_token_is_exchanged_from_client_credentials_and_cached():
    sm = _OAuthSecrets()
    calls = {"n": 0}

    def fake_post(url, payload, headers, timeout):
        calls["n"] += 1
        # Basic auth of client_id:client_secret, and the client-credentials grant body.
        assert headers["Authorization"].startswith("Basic ")
        assert b"grant_type=client_credentials" in payload
        return json.dumps({"access_token": "wtok-123", "expires_in": 3600})

    a = get_databricks_oauth_token("s", client=sm, now=0.0, http_post=fake_post)
    b = get_databricks_oauth_token("s", client=sm, now=10.0, http_post=fake_post)
    assert a == b == "wtok-123"
    assert calls["n"] == 1, "the token was re-exchanged inside its lifetime"


def test_oauth_token_is_refreshed_after_it_expires():
    sm = _OAuthSecrets()
    seq = iter(["first", "second"])

    def fake_post(url, payload, headers, timeout):
        return json.dumps({"access_token": next(seq), "expires_in": 100})

    first = get_databricks_oauth_token("s", client=sm, now=0.0, http_post=fake_post)
    later = get_databricks_oauth_token("s", client=sm, now=200.0, http_post=fake_post)
    assert first == "first" and later == "second", "an expired OAuth token was not refreshed"


def test_oauth_failure_is_opaque_and_never_leaks_the_token_endpoint():
    endpoint = "https://dbc-secret.cloud.databricks.com/oidc/v1/token"
    sm = _OAuthSecrets(token_endpoint=endpoint)

    def boom(*_a):
        raise ConnectionError(f"refused to {endpoint}")

    with pytest.raises(FraudScoreError) as excinfo:
        get_databricks_oauth_token("s", client=sm, now=0.0, http_post=boom)
    assert endpoint not in str(excinfo.value)
    assert "databricks" not in str(excinfo.value).lower()


def test_oauth_misconfigured_credentials_are_refused():
    class _BadSecrets:
        def get_secret_value(self, SecretId):  # noqa: N803
            return {"SecretString": "not-json"}

    with pytest.raises(FraudScoreError, match="misconfigured"):
        get_databricks_oauth_token("s", client=_BadSecrets(), now=0.0)


_FEATURES = {
    "amount_usd": 250.0,
    "txn_velocity_1h": 9,
    "country_mismatch": True,
    "is_unusual_hour": True,
}


class _FakeS3:
    """Minimal S3 client: get_object returns the configured body and counts calls."""

    def __init__(self, table):
        self.body = json.dumps(table).encode("utf-8")
        self.calls = 0

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3's parameter names
        self.calls += 1
        return {"Body": _Body(self.body)}


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def test_s3_feature_store_resolves_an_id_to_its_feature_vector():
    table = {"txn-1": {"card_hash": "card-a", **_FEATURES}}
    s3 = _FakeS3(table)
    store = S3OnlineFeatureStore("b", "k", s3_client=s3, now=lambda: 0.0)

    features = store.fetch(card_hash="card-a", transaction_id="txn-1")

    assert features == _FEATURES, "card_hash must be stripped and the 15 features returned"


def test_s3_feature_store_caches_the_table_across_warm_invocations():
    s3 = _FakeS3({"txn-1": {**_FEATURES}})
    store = S3OnlineFeatureStore("b", "k", s3_client=s3, now=lambda: 0.0)
    store.fetch(card_hash="x", transaction_id="txn-1")
    store.fetch(card_hash="x", transaction_id="txn-1")
    assert s3.calls == 1, "the feature object was re-downloaded inside the TTL"


def test_s3_feature_store_refuses_an_unknown_transaction():
    """Never fabricate a score for an id the store has never seen."""
    store = S3OnlineFeatureStore("b", "k", s3_client=_FakeS3({}), now=lambda: 0.0)
    with pytest.raises(FeatureStoreError, match="no online features"):
        store.fetch(card_hash="x", transaction_id="ghost")


def test_s3_feature_store_refuses_a_mismatched_card_hash():
    """A wrong id pairing must not silently return another transaction's features."""
    table = {"txn-1": {"card_hash": "card-a", **_FEATURES}}
    store = S3OnlineFeatureStore("b", "k", s3_client=_FakeS3(table), now=lambda: 0.0)
    with pytest.raises(FeatureStoreError, match="card_hash does not match"):
        store.fetch(card_hash="WRONG", transaction_id="txn-1")


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
