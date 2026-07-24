# Bedrock Integration — Action Group & Cross-Cloud Contract

> Status: design spec. Finalized and tested in Phase 7 (Bedrock zone).
> This is the single integration point between the two clouds.

## The contract: get_fraud_score()

The Bedrock Agent reaches the model ONLY through this tool. It never reads Delta
tables, never reads raw data, never knows Mosaic internals.

### Action group: `FraudScoring`

Backed by a Lambda function inside the AWS account. The Lambda:
1. Receives lookup keys from the agent.
2. Fetches online features from the Mosaic AI Feature Store.
3. Calls the Mosaic AI Model Serving REST endpoint (via private connectivity).
4. Returns the score + feature importance to the agent.

### Tool input schema

```json
{
  "transaction_id": "string",
  "card_hash": "string"
}
```

### Tool output schema

```json
{
  "fraud_score": 0.87,
  "model_version": "fraud-xgb:23",
  "threshold": 0.70,
  "decision_hint": "review",
  "top_features": [
    {"name": "txn_velocity_1h", "value": 9, "contribution": 0.31},
    {"name": "country_mismatch", "value": true, "contribution": 0.22}
  ]
}
```

The `top_features` are the *input* to the agent's reasoning, not a substitute for it.

## Knowledge Base (RAG)

- Sources: verbatim EUR-Lex regulation — AMLD5 (2015/849), GDPR (2016/679), PSD2 (2015/2366), and RTS on SCA (2018/389). Vector store: OpenSearch Serverless (`fintelliguard-reg`), embeddings `amazon.titan-embed-text-v2:0`.
- Ingestion pipeline with document versioning and chunk-level metadata.
- The agent grounds every verdict in retrieved regulatory text — no ungrounded claims.

## Guardrails

- PII redaction on inputs and outputs.
- Denied-topics and grounding checks on the generated verdict.
- A verdict that fails grounding is not returned — it is escalated to human review.

## Models

- **Wired default: Amazon Nova Lite** (`eu.amazon.nova-lite-v1:0`) — Anthropic streaming models need a one-time account use-case approval.
- `claude-haiku-4-5` switchable in dev once Anthropic access is granted.
- `claude-sonnet` for the final verdict is design-spec only (not wired).
- Bedrock model evaluation measures verdict quality against a labeled eval set.

## Security & connectivity

- Mosaic endpoint reached via private VPC connectivity, never public.
- Databricks token stored in AWS Secrets Manager, fetched at runtime.
- Least-privilege IAM on the Lambda; CloudTrail logs every invocation.
- Every agent step traced in LangSmith for audit.
