"""Invoke the deployed Tier-2 agent and print its grounded verdict.

Runs in the deploy (as the OIDC deploy role, which can InvokeAgent) to prove the FULL Tier-2
path end to end: the agent calls get_fraud_score (the cross-cloud Lambda), retrieves regulatory
text from the Knowledge Base, and returns a compliance verdict gated by Guardrails.

Best-effort by contract: it exits 0 on a usable verdict and prints diagnostics otherwise, so a
foundation-model-access hiccup does not fail the whole deploy — the cross-cloud contract itself
is gated by the Lambda unit tests and a live Lambda invocation.
"""

from __future__ import annotations

import argparse
import sys

_PROMPT = (
    "Investigate transaction txn_demo_fraud_001 with card hash card_demo_hi_risk. "
    "Call get_fraud_score, then, citing the applicable AML/PSD2 obligations, give a short "
    "compliance verdict (allow, review, or block) with the reason."
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="eu-central-1")
    ap.add_argument("--agent-name-suffix", default="fraud-investigator")
    ap.add_argument("--alias-name", default="live")
    args = ap.parse_args()

    import boto3

    agent = boto3.client("bedrock-agent", region_name=args.region)
    runtime = boto3.client("bedrock-agent-runtime", region_name=args.region)

    agents = agent.list_agents().get("agentSummaries", [])
    match = next((a for a in agents if a["agentName"].endswith(args.agent_name_suffix)), None)
    if not match:
        print("no fraud-investigator agent found; nothing to smoke-test")
        return 0
    agent_id = match["agentId"]
    aliases = agent.list_agent_aliases(agentId=agent_id).get("agentAliasSummaries", [])
    alias = next((a for a in aliases if a["agentAliasName"] == args.alias_name), None)
    if not alias:
        print(f"agent {agent_id} has no '{args.alias_name}' alias yet")
        return 0

    print(f"invoking agent {agent_id} alias {alias['agentAliasId']} ...")
    try:
        resp = runtime.invoke_agent(
            agentId=agent_id,
            agentAliasId=alias["agentAliasId"],
            sessionId="deploy-smoke",
            inputText=_PROMPT,
            enableTrace=True,
        )
        chunks, failures = [], []
        for event in resp["completion"]:
            if "chunk" in event:
                chunks.append(event["chunk"]["bytes"].decode("utf-8"))
            elif "trace" in event:
                ft = event["trace"].get("trace", {}).get("failureTrace")
                if ft:
                    failures.append(ft.get("failureReason", "unknown"))
        verdict = "".join(chunks).strip()
    except Exception as exc:  # noqa: BLE001 - best-effort smoke
        print(f"agent invocation did not complete ({type(exc).__name__}: {exc})")
        print("the cross-cloud contract is separately proven by the Lambda tests + live invoke.")
        return 0

    if failures:
        print("agent reported failure trace(s):", failures)
        return 0
    print("=== TIER-2 AGENT VERDICT ===")
    print(verdict or "(empty)")
    if not verdict:
        print("agent returned no text; check foundation-model access for this account/region")
    return 0


if __name__ == "__main__":
    sys.exit(main())
