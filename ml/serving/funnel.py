"""The decisioning funnel — the automatic glue the three tiers were missing.

Tier 1 scores EVERY transaction; the ~99% that clear are done. A transaction the model flags
(`decision_hint` review or block) is ESCALATED — automatically, no human in the loop — to the
Tier-2 Bedrock agent, which produces a documented compliance verdict grounded in AML/PSD2.

The tiers already existed as independent endpoints (score here, invoke the agent there), but
nothing connected them: a flagged transaction only got a verdict if a human went and asked for
one. This is the orchestration that makes "a suspicious transaction is justified automatically"
true — the funnel, not three parts on a shelf.

The core (`FraudFunnel`) is pure and injectable so the escalation policy is unit-tested without
any cloud; `build_default_funnel` / `main` wire the real Tier-1 serving endpoint and the Tier-2
agent for the live demo.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The decisions that escalate to a documented Tier-2 verdict. "allow" clears at Tier 1.
FLAGGED_DECISIONS = ("review", "block")

# transaction_id -> {card_hash, ...15 features}. The same seed the Tier-2 online store uses,
# parity-tested against infra/aws/online_features.tf.
_DEMO = Path(__file__).resolve().parents[1].parent / "agents" / "databricks" / "demo_transactions.json"

ScoreFn = Callable[[str, str], dict[str, Any]]
VerdictFn = Callable[[str, str], str]


@dataclass
class FunnelResult:
    """What the funnel decided for one transaction."""

    transaction_id: str
    fraud_score: float
    decision: str
    escalated: bool
    top_features: list[dict[str, Any]] = field(default_factory=list)
    verdict: str | None = None  # populated ONLY when escalated


class FraudFunnel:
    """Tier 1 -> (auto-escalate the flagged) -> Tier 2. No prompt, no human trigger."""

    def __init__(
        self,
        score_fn: ScoreFn,
        verdict_fn: VerdictFn,
        *,
        flagged: tuple[str, ...] = FLAGGED_DECISIONS,
    ) -> None:
        self._score_fn = score_fn
        self._verdict_fn = verdict_fn
        self._flagged = {d.lower() for d in flagged}

    def run(self, transaction_id: str, card_hash: str) -> FunnelResult:
        """Score the transaction; if the model flags it, escalate for a documented verdict."""
        score = self._score_fn(transaction_id, card_hash)
        decision = str(score.get("decision_hint", "")).lower()
        escalated = decision in self._flagged
        # The verdict is produced ONLY for flagged transactions — the ~99% that clear never pay
        # for a Tier-2 call, which is the whole point of the funnel.
        verdict = self._verdict_fn(transaction_id, card_hash) if escalated else None
        return FunnelResult(
            transaction_id=transaction_id,
            fraud_score=float(score.get("fraud_score", 0.0)),
            decision=decision,
            escalated=escalated,
            top_features=list(score.get("top_features", [])),
            verdict=verdict,
        )


# --------------------------------------------------------------------------------------------
# Live wiring — the real Tier-1 serving endpoint + the Tier-2 Bedrock agent. Lazy imports so
# the pure core (and its tests) need neither the Databricks SDK nor boto3.
# --------------------------------------------------------------------------------------------


def _load_features(transaction_id: str) -> dict[str, Any]:
    table = json.loads(_DEMO.read_text(encoding="utf-8"))
    record = table.get(transaction_id)
    if not record:
        raise SystemExit(f"unknown demo transaction {transaction_id!r}; known: {list(table)}")
    return {k: v for k, v in record.items() if k != "card_hash"}


def _serving_score_fn(region_host: str | None = None) -> ScoreFn:
    """Query the live Tier-1 fraud-score serving endpoint (resolved by name)."""
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    endpoints = [e.name for e in w.serving_endpoints.list() if e.name.endswith("fintelliguard-fraud-score")]
    if not endpoints:
        raise SystemExit("no fintelliguard-fraud-score serving endpoint found — is the stack deployed?")
    endpoint = endpoints[0]

    def score(transaction_id: str, card_hash: str) -> dict[str, Any]:
        features = _load_features(transaction_id)
        resp = w.serving_endpoints.query(name=endpoint, dataframe_records=[features])
        pred = resp.predictions[0] if resp.predictions else {}
        return pred if isinstance(pred, dict) else {}

    return score


def _agent_verdict_fn(region: str) -> VerdictFn:
    """Invoke the Tier-2 Bedrock agent for a grounded compliance verdict."""
    import boto3

    agent = boto3.client("bedrock-agent", region_name=region)
    runtime = boto3.client("bedrock-agent-runtime", region_name=region)
    summaries = agent.list_agents().get("agentSummaries", [])
    match = next((a for a in summaries if a["agentName"].endswith("fraud-investigator")), None)
    if not match:
        raise SystemExit("no fraud-investigator agent found — deploy with layers=full")
    agent_id = match["agentId"]
    aliases = agent.list_agent_aliases(agentId=agent_id).get("agentAliasSummaries", [])
    alias = next((a for a in aliases if a["agentAliasName"] == "live"), None)
    alias_id = alias["agentAliasId"] if alias else "TSTALIASID"

    def verdict(transaction_id: str, card_hash: str) -> str:
        prompt = (
            f"Produce a compliance verdict for transaction {transaction_id} "
            f"(card_hash {card_hash}). Call get_fraud_score, cite the applicable AML/PSD2 "
            "obligations from the knowledge base, and recommend allow, review, or block."
        )
        resp = runtime.invoke_agent(
            agentId=agent_id, agentAliasId=alias_id, sessionId=f"funnel-{transaction_id}", inputText=prompt
        )
        return "".join(
            ev["chunk"]["bytes"].decode("utf-8") for ev in resp["completion"] if "chunk" in ev
        ).strip()

    return verdict


def build_default_funnel(region: str = "eu-central-1") -> FraudFunnel:
    return FraudFunnel(_serving_score_fn(), _agent_verdict_fn(region))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run one transaction through the decisioning funnel.")
    ap.add_argument("--transaction-id", default="txn_demo_fraud_001")
    ap.add_argument("--card-hash", default="card_demo_hi_risk")
    ap.add_argument("--region", default="eu-central-1")
    args = ap.parse_args(argv)

    print(f"→ transaction {args.transaction_id}: scoring at Tier 1 ...")
    result = build_default_funnel(args.region).run(args.transaction_id, args.card_hash)
    print(f"  fraud_score = {result.fraud_score:.4f}  →  decision = {result.decision.upper()}")
    for f in result.top_features[:5]:
        print(f"    · {f.get('name')} = {f.get('value')}  (contribution {f.get('contribution'):+.2f})")
    if not result.escalated:
        print("  cleared at Tier 1 — no escalation. ✅")
        return 0
    print("  FLAGGED → auto-escalating to the Tier-2 compliance agent ...\n")
    print("=== DOCUMENTED VERDICT (Tier 2, grounded in AML/PSD2) ===")
    print(result.verdict or "(no verdict returned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
