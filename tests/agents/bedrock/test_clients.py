"""Client tests: Secrets Manager token (mocked), Mosaic client auth + error handling."""

from __future__ import annotations

import json

import pytest
from clients import MosaicModelServingClient, get_databricks_token
from errors import FraudScoreError


def test_get_databricks_token_from_secrets_manager():
    fake_client = type(
        "FakeSM",
        (),
        {"get_secret_value": lambda self, SecretId: {"SecretString": f"tok-for-{SecretId}"}},  # noqa: N803
    )()
    token = get_databricks_token("fintelliguard/dev/databricks/token", client=fake_client)
    assert token == "tok-for-fintelliguard/dev/databricks/token"


def test_mosaic_client_sends_bearer_token_and_returns_contract():
    captured = {}

    def fake_post(url, payload, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json.loads(payload.decode("utf-8"))
        return json.dumps({"predictions": [{"fraud_score": 0.9, "decision_hint": "block"}]})

    client = MosaicModelServingClient(
        "https://mosaic.example/serving/fraud",
        token_provider=lambda: "secret-token-123",
        http_post=fake_post,
    )
    result = client.get_fraud_score({"amount_usd": 10.0})

    assert result == {"fraud_score": 0.9, "decision_hint": "block"}
    assert captured["headers"]["Authorization"] == "Bearer secret-token-123"
    assert captured["payload"] == {"dataframe_records": [{"amount_usd": 10.0}]}


def test_mosaic_client_wraps_transport_failure():
    def boom(url, payload, headers, timeout):
        raise ConnectionError("connection refused")

    client = MosaicModelServingClient(
        "https://mosaic.example", token_provider=lambda: "t", http_post=boom
    )
    with pytest.raises(FraudScoreError, match="Mosaic Model Serving call failed"):
        client.get_fraud_score({"amount_usd": 1.0})


def test_no_hardcoded_secret_in_default_path():
    # The token always comes through the provider; constructing the client must not need
    # a secret value baked in.
    client = MosaicModelServingClient("https://x", token_provider=lambda: "from-vault")
    assert callable(client._token_provider)
    assert client._token_provider() == "from-vault"
