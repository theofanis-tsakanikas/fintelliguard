# agents/bedrock/ — Tier 2 (real-time verdict)

AWS **Bedrock Agent** + **Knowledge Bases** + **Guardrails** + action groups.

Scores only suspicious transactions (~1%, high XGBoost score) and produces a compliance
**verdict + documented explanation** grounded in regulatory text (RAG). Every verdict
passes through Guardrails (PII redaction + hallucination guard) and is traced in
LangSmith for audit — **no verdict ships ungrounded**.

The action group calls `get_fraud_score()` against the Mosaic Model Serving endpoint
(private VPC) — the only way Bedrock touches the model. Dev uses `claude-haiku`;
`claude-sonnet` for final evaluation only. See `@docs/bedrock-integration.md`.
