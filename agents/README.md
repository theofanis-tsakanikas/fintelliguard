# agents/

The GenAI layers — **two zones**, connected by a contract, not coupling.

- `bedrock/` — **Tier 2**, AWS edge: real-time compliance verdict on suspicious
  transactions (~1%) via RAG + Guardrails. The slow reasoning layer, not the scorer.
- `databricks/` — **Tier 3**, lakehouse: async human-driven fraud-analyst copilot
  (Agent Framework + Genie + Vector Search + `get_fraud_score()`).
- `langgraph/` — self-healing Supervisor + Medic, traced in LangSmith.

See `@docs/bedrock-integration.md` and `@docs/copilot-design.md`.
