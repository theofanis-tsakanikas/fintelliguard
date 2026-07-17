"""The copilot's `get_fraud_score` tool — the SAME contract Bedrock's action group uses.

The copilot has a transaction, not a feature vector: an analyst asks "why was THIS
transaction flagged?" and holds a `transaction_id` and a `card_hash`. So this tool takes
those two — matching the schema `agents/databricks/agent.py` declares AND the cross-cloud
contract in `docs/bedrock-integration.md` — resolves them to the 15 features through an
injected Feature Store lookup, and scores.

That resolution used to be missing. The tool declared `{transaction_id, card_hash}` and the
implementation was `get_fraud_score(features: dict)` — a one-line passthrough that took a
feature vector nobody had and elided the ID -> features lookup that is the tool's entire
job. The declaration and the implementation disagreed, and `test_tool_input_schemas_match_
contracts` "matched" them by re-asserting the same literals in both files.

The lookup is injected, exactly as the Bedrock Lambda's `OnlineFeatureStore` is (`agents/
bedrock/lambda/clients.py`): the live Mosaic online store in production, a local stub in
tests. The online store itself is deferred to the Databricks deploy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# transaction_id, card_hash -> the 15-feature vector. The seam the online Feature Store
# fills; without it the copilot cannot turn an id into something scoreable.
FeatureLookup = Callable[[str, str], dict[str, Any]]


class FeatureLookupError(RuntimeError):
    """The features for a transaction could not be resolved — never silently scored on
    empty or partial input."""


class FraudScoreTool:
    """Resolve a transaction id to features, then return the get_fraud_score contract."""

    def __init__(self, score_fn: Callable[[dict[str, Any]], dict[str, Any]], lookup: FeatureLookup):
        self._score_fn = score_fn
        self._lookup = lookup

    def get_fraud_score(self, transaction_id: str, card_hash: str) -> dict[str, Any]:
        """Look up the transaction's features and score them.

        Signature is `(transaction_id, card_hash)` — the tool's declared schema and the
        contract Bedrock calls — not `(features)`. The copilot never holds a feature vector.
        """
        if not transaction_id or not card_hash:
            raise FeatureLookupError(
                "get_fraud_score needs both transaction_id and card_hash; the copilot has "
                "the ids and the Feature Store resolves the features"
            )
        features = self._lookup(transaction_id, card_hash)
        if not features:
            raise FeatureLookupError(
                f"no features for transaction {transaction_id} — scoring an empty vector "
                "would return a fabricated score for a transaction that was never featurised"
            )
        return self._score_fn(features)
