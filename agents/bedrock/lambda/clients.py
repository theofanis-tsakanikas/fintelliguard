"""Injectable clients for the FraudScoring Lambda.

The Feature Store and Mosaic endpoint clients are injectable so the handler is testable
with mocks. The Databricks token is read from AWS Secrets Manager at runtime — never
hardcoded. Stdlib only (boto3 is provided by the Lambda runtime, imported lazily).
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable, Protocol

from errors import FeatureStoreError, FraudScoreError


class FeatureStore(Protocol):
    """Resolves the canonical 15 features for a transaction by lookup key."""

    def fetch(self, *, card_hash: str, transaction_id: str) -> dict[str, Any]: ...


class FraudScoreService(Protocol):
    """Returns the get_fraud_score contract for a feature vector."""

    def get_fraud_score(self, features: dict[str, Any]) -> dict[str, Any]: ...


def get_databricks_token(secret_id: str, *, client: Any | None = None) -> str:
    """Fetch the Databricks token from Secrets Manager (client injectable for tests)."""
    if client is None:
        import boto3  # provided by the Lambda runtime

        client = boto3.client("secretsmanager")
    return client.get_secret_value(SecretId=secret_id)["SecretString"]


def _urllib_post(url: str, payload: bytes, headers: dict[str, str], timeout: float) -> str:
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


class MosaicModelServingClient:
    """Calls the Mosaic AI Model Serving REST endpoint (private VPC in prod).

    The HTTP transport and token provider are injectable. The endpoint runs the
    `ml/serving` scorer and returns the get_fraud_score contract.
    """

    def __init__(
        self,
        endpoint_url: str,
        token_provider: Callable[[], str],
        *,
        http_post: Callable[[str, bytes, dict[str, str], float], str] | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._token_provider = token_provider
        self._http_post = http_post or _urllib_post
        self._timeout = timeout

    def get_fraud_score(self, features: dict[str, Any]) -> dict[str, Any]:
        token = self._token_provider()
        payload = json.dumps({"dataframe_records": [features]}).encode("utf-8")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            raw = self._http_post(self._endpoint_url, payload, headers, self._timeout)
        except Exception as exc:  # surface any transport error as a domain error
            raise FraudScoreError(f"Mosaic Model Serving call failed: {exc}") from exc
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise FraudScoreError("Mosaic Model Serving returned invalid JSON") from exc
        return _extract_contract(data)


def _extract_contract(data: Any) -> dict[str, Any]:
    if isinstance(data, dict) and "predictions" in data:
        predictions = data["predictions"]
    elif isinstance(data, list):
        predictions = data
    else:
        predictions = [data]
    if not predictions:
        raise FraudScoreError("Mosaic Model Serving returned no prediction")
    return predictions[0]


class OnlineFeatureStore:
    """Default online Feature Store lookup — deployed on Databricks (deferred).

    A `lookup` callable can be injected for local use/tests; without one, lookups raise
    (the online store lives in the Databricks deploy phase).
    """

    def __init__(self, lookup: Callable[[str, str], dict[str, Any]] | None = None) -> None:
        self._lookup = lookup

    def fetch(self, *, card_hash: str, transaction_id: str) -> dict[str, Any]:
        if self._lookup is None:
            raise FeatureStoreError(
                "online Feature Store lookup is deferred to the Databricks deploy"
            )
        return self._lookup(card_hash, transaction_id)
