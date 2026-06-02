# agents/databricks/ — Tier 3 (analyst copilot)

Mosaic AI **Agent Framework** copilot for human fraud analysts — **async, not automated**.

Routes between three tools, each a first-class engineering artifact (tool descriptions
matter):

- **Genie** — NL→SQL for precise facts.
- **Vector Search** — semantic similar-case retrieval.
- **`get_fraud_score()`** — the shared model endpoint.

Routing quality is measured with **Mosaic AI Agent Evaluation**. See
`@docs/copilot-design.md`.
