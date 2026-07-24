# Analyst Copilot — Design (Databricks Mosaic AI)

> Status: design spec. Built and evaluated in Phase 9 (copilot zone).
> Tier 3: human-driven investigation of flagged fraud cases.

## Purpose

A fraud analyst opens a flagged case and investigates in natural language. The
copilot is a multi-tool agent (Mosaic AI Agent Framework), not a single RAG chain.

## Tools

The agent's LLM selects tools via tool-use, guided by descriptions + a routing
system prompt. Tool descriptions are first-class engineering.

| Tool | Use for | Mechanism |
|---|---|---|
| `query_lakehouse` *(deferred)* | Precise structured facts: counts, sums, merchant history | Genie NL→SQL over Unity Catalog — not provisioned yet |
| `search_similar_cases` | "Cases like this one" — semantic similarity | Vector Search over case embeddings (RAG) |
| `get_fraud_score` | The model's score + which features drove it | Same endpoint Bedrock uses |

Routing principle: structured/exact → `query_lakehouse`; similarity/"like this" →
`search_similar_cases`; "why flagged" → `get_fraud_score`. The agent may chain tools.

> **Live vs deferred:** `search_similar_cases` and `get_fraud_score` run live; `query_lakehouse`
> (Genie) is deferred — no SQL warehouse / Genie space is provisioned, so the served copilot
> runs the other two tools.

## Vector index

- Embeds historical resolved fraud cases (features + analyst notes + outcome).
- Stored in Databricks Vector Search, governed by Unity Catalog.
- Refreshed as new cases are resolved.

## Evaluation (Mosaic AI Agent Evaluation)

Routing is LLM judgment, so quality must be measured, not assumed.

- **Tool-selection accuracy** — did it pick the right tool for the question?
- **Correctness** — for SQL answers, against ground truth.
- **Groundedness** — for retrieval answers, against retrieved evidence.
- **Relevance** — does the answer address the question?

A labeled eval set of representative analyst questions gates changes to prompts or
tool descriptions.

## Governance

- The copilot inherits Unity Catalog RBAC — analysts only see data they may see.
- Every session traced in LangSmith.
