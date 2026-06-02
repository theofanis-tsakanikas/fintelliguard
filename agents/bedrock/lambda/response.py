"""Bedrock action-group response envelope (function schema).

A success carries the `get_fraud_score` contract JSON; a failure sets `responseState =
FAILURE` so the agent can handle it gracefully.
"""

from __future__ import annotations

import json
from typing import Any

MESSAGE_VERSION = "1.0"
_DEFAULT_ACTION_GROUP = "FraudScoring"
_DEFAULT_FUNCTION = "get_fraud_score"


def build_success_response(event: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Wrap the get_fraud_score contract in the action-group response envelope."""
    return _function_response(event, json.dumps(contract))


def build_failure_response(event: dict[str, Any], message: str) -> dict[str, Any]:
    """Return a FAILURE envelope the agent can reason about, not a raw exception."""
    return _function_response(event, json.dumps({"error": message}), state="FAILURE")


def _function_response(
    event: dict[str, Any], body: str, state: str | None = None
) -> dict[str, Any]:
    function_response: dict[str, Any] = {"responseBody": {"TEXT": {"body": body}}}
    if state is not None:
        function_response["responseState"] = state
    return {
        "messageVersion": MESSAGE_VERSION,
        "response": {
            "actionGroup": event.get("actionGroup", _DEFAULT_ACTION_GROUP),
            "function": event.get("function", _DEFAULT_FUNCTION),
            "functionResponse": function_response,
        },
    }
