# agents/bedrock/ — Tier-2 compliance-verdict zone

The AWS edge zone: a **Bedrock Agent** produces a regulation-grounded compliance verdict
for suspicious transactions (~1%). It calls back to Mosaic for the score via the
**FraudScoring action group** (a Lambda), grounds the verdict in a **Knowledge Base**
(AML/PSD2), and is gated by **Guardrails**. See `docs/bedrock-integration.md`.

> **What runs where.** The action-group **Lambda bridge** is plain Python, **tested
> locally** with mocks. The agent/KB/guardrail are **Terraform, offline-validated**. The
> **LLM reasoning, KB retrieval, and guardrail enforcement run only on Bedrock** and are
> **deferred to the deploy phase** (no `plan`/`apply` here).

## Layout

| Path | What |
|---|---|
| `lambda/` | FraudScoring handler (`handler.lambda_handler`) — parses the action-group event, fetches online features (injectable), calls Mosaic `get_fraud_score` (injectable; Databricks token from Secrets Manager), returns the contract in the response envelope. Flat modules (the zip root; `lambda` is a Python keyword), stdlib-only. |
| `instructions/` | `fraud_investigator_v1.md` — versioned system prompt (tool use → grounding → structured verdict). |
| `kb/` | Tested doc-prep **chunking** (`chunking.py`) + a small sample corpus. Actual KB ingestion is cloud/deferred. |
| `terraform/` | Agent, action group, Knowledge Base (OpenSearch Serverless), guardrail, IAM — consuming `infra/aws` via remote state. |

## The contract (Lambda)

The Lambda returns the **exact** `get_fraud_score` contract
(`fraud_score, model_version, threshold, decision_hint, top_features[]`) wrapped in the
Bedrock function-response envelope; failures return `responseState = FAILURE` so the agent
can react. The Databricks token is read from **Secrets Manager at runtime — never
hardcoded**; the Mosaic HTTP client and Feature Store client are injectable, so the
handler is fully unit-tested.

```bash
pytest tests/agents          # handler, clients/auth, KB chunking
ruff check .
```

## Terraform

Consumes `infra/aws` outputs via remote state: the **Mosaic-Lambda IAM role**
(`lambda_role_arn`), **KMS** key (corpus encryption), **Secrets** (`secret_arns`), and
**private subnets + Lambda SG** (so the Lambda reaches Mosaic privately). State key
`agents/bedrock/terraform.tfstate`, eu-central-1.

```bash
cd agents/bedrock/terraform
terraform init -backend=false
terraform fmt -check -recursive
terraform validate      # offline; no plan/apply
```

**Provider coverage.** Current `hashicorp/aws` (≥ 5.40) covers everything used —
`aws_bedrockagent_agent`, `aws_bedrockagent_agent_action_group` (function schema),
`aws_bedrockagent_knowledge_base` + `aws_bedrockagent_data_source` (S3 →
OpenSearch Serverless), `aws_bedrockagent_agent_knowledge_base_association`,
`aws_bedrockagent_agent_alias`, and `aws_bedrock_guardrail` (PII / denied-topics /
contextual-grounding policies). **No thin fallback was required.** Dev uses Claude Haiku
(`foundation_model`); switch to Sonnet for final evaluation.

## Deferred to deploy

KB ingestion (uploading the corpus to S3 + vector sync), agent preparation, real model
invocation, guardrail enforcement, and the Lambda's online Feature Store lookup all run on
the cloud and are deferred. This layer ships the tested bridge + validated infrastructure.
