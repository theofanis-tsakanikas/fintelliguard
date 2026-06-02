"""FraudScoring action-group Lambda — the bridge from the Bedrock agent to Mosaic.

Flow: parse the action-group event ({transaction_id, card_hash}) -> fetch the 15 online
features -> call Mosaic `get_fraud_score` -> return the contract in the response envelope.
Dependencies are injectable so this is fully unit-testable without AWS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from clients import (
    FeatureStore,
    FraudScoreService,
    MosaicModelServingClient,
    OnlineFeatureStore,
    get_databricks_token,
)
from errors import ActionGroupError, FeatureStoreError, FraudScoreError
from response import build_failure_response, build_success_response

_REQUIRED_PARAMS = ("transaction_id", "card_hash")


@dataclass
class Dependencies:
    """Injected collaborators (defaults built from env in `_default_dependencies`)."""

    feature_store: FeatureStore
    fraud_score: FraudScoreService


def parse_event(event: dict) -> dict[str, str]:
    """Extract the required keys from a function- or OpenAPI-style action-group event."""
    params = _function_params(event) or _openapi_params(event)
    keys = {name: params.get(name) for name in _REQUIRED_PARAMS}
    missing = [name for name in _REQUIRED_PARAMS if not keys.get(name)]
    if missing:
        raise ActionGroupError(f"missing required parameter(s): {missing}")
    return {name: str(keys[name]) for name in _REQUIRED_PARAMS}


def _function_params(event: dict) -> dict[str, object]:
    return {p.get("name"): p.get("value") for p in (event.get("parameters") or []) if p.get("name")}


def _openapi_params(event: dict) -> dict[str, object]:
    try:
        properties = event["requestBody"]["content"]["application/json"]["properties"]
    except (KeyError, TypeError):
        return {}
    return {p.get("name"): p.get("value") for p in properties if p.get("name")}


def lambda_handler(
    event: dict, context: object = None, *, dependencies: Dependencies | None = None
) -> dict:
    """AWS Lambda entry point for the FraudScoring action group."""
    deps = dependencies or _default_dependencies()
    try:
        keys = parse_event(event)
        features = deps.feature_store.fetch(
            card_hash=keys["card_hash"], transaction_id=keys["transaction_id"]
        )
        contract = deps.fraud_score.get_fraud_score(features)
    except (ActionGroupError, FeatureStoreError, FraudScoreError) as exc:
        return build_failure_response(event, str(exc))
    return build_success_response(event, contract)


def _default_dependencies() -> Dependencies:
    endpoint_url = os.environ["MOSAIC_ENDPOINT_URL"]
    secret_id = os.environ["DATABRICKS_TOKEN_SECRET_ID"]
    fraud_score = MosaicModelServingClient(endpoint_url, lambda: get_databricks_token(secret_id))
    return Dependencies(feature_store=OnlineFeatureStore(), fraud_score=fraud_score)
