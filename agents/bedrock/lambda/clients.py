"""Injectable clients for the FraudScoring Lambda.

The Feature Store and Mosaic endpoint clients are injectable so the handler is testable
with mocks. The Databricks token is read from AWS Secrets Manager at runtime — never
hardcoded. Stdlib only (boto3 is provided by the Lambda runtime, imported lazily).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from errors import FeatureStoreError, FraudScoreError

# The contract the endpoint must return. Mirrors ml.serving.scorer.CONTRACT_KEYS — the
# Lambda ships as a standalone zip and cannot import the ml package, so the tuple is
# duplicated here and a test asserts the two never drift.
_CONTRACT_KEYS = ("fraud_score", "model_version", "threshold", "decision_hint", "top_features")

_MAX_ATTEMPTS = 2  # one retry; the Lambda timeout (10s) and HTTP timeout (5s) leave room
_BACKOFF_BASE = 0.2
_SLEEP = time.sleep  # injectable indirection so tests do not actually wait
_LOGGER = logging.getLogger("fintelliguard.fraud_scoring")

# The Databricks token, cached at MODULE scope across warm invocations. It was fetched from
# Secrets Manager on EVERY request — a fresh boto3 client and a GetSecretValue call each
# time — adding 20-50ms to a Tier-2 call and, at scale, hitting the account-wide
# GetSecretValue throttle, which surfaced to the agent as a scoring outage.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_TTL_SECONDS = 300.0


class FeatureStore(Protocol):
    """Resolves the canonical 15 features for a transaction by lookup key."""

    def fetch(self, *, card_hash: str, transaction_id: str) -> dict[str, Any]: ...


class FraudScoreService(Protocol):
    """Returns the get_fraud_score contract for a feature vector."""

    def get_fraud_score(self, features: dict[str, Any]) -> dict[str, Any]: ...


_SECRETS_CLIENT: Any | None = None


def _secrets_client() -> Any:
    """One boto3 client for the module's warm lifetime, not one per request.

    Re-creating the client per call re-does credential resolution every time; hoisting it
    is the standard Lambda pattern and it was not being followed.
    """
    global _SECRETS_CLIENT
    if _SECRETS_CLIENT is None:
        import boto3  # provided by the Lambda runtime

        _SECRETS_CLIENT = boto3.client("secretsmanager")
    return _SECRETS_CLIENT


def get_databricks_token(
    secret_id: str, *, client: Any | None = None, now: float | None = None
) -> str:
    """The Databricks token, cached for `_TOKEN_TTL_SECONDS` across warm invocations.

    Injectable `client`/`now` for tests. The cache is keyed by secret id so a rotation to a
    different secret is picked up, and it expires so an in-place rotation of the same secret
    is picked up within the TTL rather than never.
    """
    stamp = now if now is not None else time.monotonic()
    cached = _TOKEN_CACHE.get(secret_id)
    if cached is not None and stamp - cached[1] < _TOKEN_TTL_SECONDS:
        return cached[0]

    resolved = client or _secrets_client()
    token = resolved.get_secret_value(SecretId=secret_id)["SecretString"]
    _TOKEN_CACHE[secret_id] = (token, stamp)
    return token


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

        raw = self._post_with_retry(payload, headers)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            # The raw body is logged, never returned: a Databricks error page can carry
            # internal detail, and the caller's message becomes the agent's context.
            _LOGGER.error("Mosaic returned non-JSON: %r", raw[:500])
            raise FraudScoreError("the scoring service returned an unreadable response") from exc
        return _extract_contract(data)

    def _post_with_retry(self, payload: bytes, headers: dict[str, str]) -> str:
        """One bounded retry with jitter. A single 503 must not fail a whole verdict.

        The exception is caught, LOGGED with its detail, and re-raised as an OPAQUE domain
        error. `FraudScoreError(f"... {exc}")` used to embed the exception, and a urllib
        HTTPError/URLError stringifies to include the ENDPOINT URL — which flowed through
        `handler.py` into `response.py`'s `{"error": message}` and straight into the agent's
        context. That is the private Mosaic URL crossing the cross-cloud boundary CLAUDE.md
        says Bedrock must never know, into regulated output.
        """
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return self._http_post(self._endpoint_url, payload, headers, self._timeout)
            except Exception as exc:  # noqa: BLE001 - transport errors are retried then opaqued
                last = exc
                _LOGGER.warning("Mosaic call attempt %d failed: %s", attempt + 1, exc)
                if attempt + 1 < _MAX_ATTEMPTS:
                    # Deterministic backoff — no wall-clock randomness in a testable client.
                    _SLEEP(_BACKOFF_BASE * (2**attempt))
        _LOGGER.error("Mosaic call failed after %d attempts: %s", _MAX_ATTEMPTS, last)
        raise FraudScoreError("the scoring service is unavailable") from last


def _extract_contract(data: Any) -> dict[str, Any]:
    """The get_fraud_score contract, VALIDATED — not `predictions[0]` blindly.

    `return predictions[0]` returned whatever the endpoint sent. So `{"error": "model
    loading"}` became a "fraud score", `build_success_response` wrapped it, and the agent
    reasoned about a scoring outage as though it were a verdict. The contract keys exist
    (`ml.serving.scorer.CONTRACT_KEYS`); a response that is not one is an error, loudly.
    """
    if isinstance(data, dict) and "predictions" in data:
        predictions = data["predictions"]
    elif isinstance(data, list):
        predictions = data
    else:
        predictions = [data]
    if not predictions:
        raise FraudScoreError("the scoring service returned no prediction")

    contract = predictions[0]
    if not isinstance(contract, dict):
        raise FraudScoreError("the scoring service returned a malformed prediction")
    missing = [key for key in _CONTRACT_KEYS if key not in contract]
    if missing:
        # The keys we got are logged (to see a shape change); never returned.
        _LOGGER.error("Mosaic contract missing %s; got keys %s", missing, sorted(contract))
        raise FraudScoreError("the scoring service returned an unexpected result shape")
    return {key: contract[key] for key in _CONTRACT_KEYS}


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
