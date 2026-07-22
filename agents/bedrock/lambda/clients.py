"""Injectable clients for the FraudScoring Lambda.

The Feature Store and Mosaic endpoint clients are injectable so the handler is testable
with mocks. The Databricks token is read from AWS Secrets Manager at runtime — never
hardcoded. Stdlib only (boto3 is provided by the Lambda runtime, imported lazily).
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.parse
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


# OAuth M2M tokens, cached by secret id until shortly before they expire. The action group
# authenticates to the serving endpoint as the deploy's SERVICE PRINCIPAL via the OAuth
# client-credentials grant — no long-lived PAT to mint, store, or rotate, and no token-creation
# entitlement to grant. The secret holds {client_id, client_secret, token_endpoint}.
_OAUTH_CACHE: dict[str, tuple[str, float]] = {}
_OAUTH_REFRESH_SKEW_SECONDS = 30.0


def get_databricks_oauth_token(
    secret_id: str,
    *,
    client: Any | None = None,
    now: float | None = None,
    http_post: Callable[[str, bytes, dict[str, str], float], str] | None = None,
) -> str:
    """A workspace OAuth token for the SP, exchanged from client credentials and cached.

    The credentials live in Secrets Manager as JSON; `client`/`now`/`http_post` are injectable
    so the exchange is unit-testable without AWS or a live token endpoint.
    """
    stamp = now if now is not None else time.monotonic()
    cached = _OAUTH_CACHE.get(secret_id)
    if cached is not None and stamp < cached[1]:
        return cached[0]

    resolved = client or _secrets_client()
    blob = resolved.get_secret_value(SecretId=secret_id)["SecretString"]
    try:
        creds = json.loads(blob)
        client_id = creds["client_id"]
        client_secret = creds["client_secret"]
        token_endpoint = creds["token_endpoint"]
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise FraudScoreError("the scoring-service credentials are misconfigured") from exc

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    grant = {"grant_type": "client_credentials", "scope": "all-apis"}
    body = urllib.parse.urlencode(grant).encode()
    poster = http_post or _urllib_post
    try:
        raw = poster(token_endpoint, body, headers, 5.0)
        data = json.loads(raw)
        token = data["access_token"]
        ttl = float(data.get("expires_in", 3600))
    except Exception as exc:  # noqa: BLE001 - any failure here is an opaque auth failure
        # Never echo the exception: a urllib error stringifies to include the token endpoint,
        # which is the workspace host Bedrock must never learn.
        _LOGGER.error("OAuth token exchange failed: %s", exc)
        raise FraudScoreError("could not authenticate to the scoring service") from exc

    _OAUTH_CACHE[secret_id] = (token, stamp + max(ttl - _OAUTH_REFRESH_SKEW_SECONDS, 0.0))
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
    """Injected-lookup Feature Store: a `lookup` callable resolves (card_hash, transaction_id)
    to the 15 features. Used with a stub in tests; `S3OnlineFeatureStore` is the deployed one.
    """

    def __init__(self, lookup: Callable[[str, str], dict[str, Any]] | None = None) -> None:
        self._lookup = lookup

    def fetch(self, *, card_hash: str, transaction_id: str) -> dict[str, Any]:
        if self._lookup is None:
            raise FeatureStoreError(
                "online Feature Store lookup is deferred to the Databricks deploy"
            )
        return self._lookup(card_hash, transaction_id)


# The online feature table, cached at MODULE scope across warm invocations — same reasoning as
# the token cache: without it every Tier-2 call re-downloads the object from S3.
_FEATURES_CACHE: dict[str, tuple[dict[str, dict[str, Any]], float]] = {}
_FEATURES_TTL_SECONDS = 300.0
_S3_CLIENT: Any | None = None


def _s3_client() -> Any:
    """One boto3 S3 client for the module's warm lifetime (the hoisted-client Lambda pattern)."""
    global _S3_CLIENT
    if _S3_CLIENT is None:
        import boto3  # provided by the Lambda runtime

        _S3_CLIENT = boto3.client("s3")
    return _S3_CLIENT


class S3OnlineFeatureStore:
    """Online feature lookup backed by a JSON object in S3: `{transaction_id: {features...}}`.

    This is the deployed online store for the demo: Terraform seeds a small, KMS-encrypted
    object with representative transactions, and the Lambda resolves an id to its 15 features
    here before scoring. A production system would serve these from the Mosaic online Feature
    Store; the SEAM is identical (id -> features), so swapping the backing store is a change to
    this class alone.

    The parsed table is cached module-scope with a TTL, and the boto3 client and clock are
    injectable so the lookup is unit-testable without AWS.
    """

    def __init__(
        self,
        bucket: str,
        key: str,
        *,
        s3_client: Any | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._bucket = bucket
        self._key = key
        self._s3_client = s3_client
        self._now = now or time.monotonic

    def _load(self) -> dict[str, dict[str, Any]]:
        cache_key = f"{self._bucket}/{self._key}"
        stamp = self._now()
        cached = _FEATURES_CACHE.get(cache_key)
        if cached is not None and stamp - cached[1] < _FEATURES_TTL_SECONDS:
            return cached[0]

        client = self._s3_client or _s3_client()
        body = client.get_object(Bucket=self._bucket, Key=self._key)["Body"].read()
        raw = body.decode("utf-8") if isinstance(body, bytes) else body
        try:
            table = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            _LOGGER.error(
                "online feature object is not valid JSON at s3://%s/%s", self._bucket, self._key
            )  # noqa: E501
            raise FeatureStoreError("the online feature store is unreadable") from exc
        if not isinstance(table, dict):
            raise FeatureStoreError("the online feature store has an unexpected shape")
        _FEATURES_CACHE[cache_key] = (table, stamp)
        return table

    def fetch(self, *, card_hash: str, transaction_id: str) -> dict[str, Any]:
        table = self._load()
        record = table.get(transaction_id)
        if not isinstance(record, dict):
            # No fabricated score for an id the store has never seen — the handler turns this
            # into a clean action-group failure the agent can reason about.
            raise FeatureStoreError(f"no online features for transaction {transaction_id!r}")
        # `card_hash` is a secondary key: if the record carries one, it must match, so a wrong
        # pairing is refused rather than silently scoring another transaction's features.
        stored_hash = record.get("card_hash")
        if stored_hash is not None and str(stored_hash) != card_hash:
            raise FeatureStoreError(f"card_hash does not match transaction {transaction_id!r}")
        return {name: value for name, value in record.items() if name != "card_hash"}
