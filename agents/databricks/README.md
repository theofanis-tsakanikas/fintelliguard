# agents/databricks/ — Tier-3 analyst copilot (logic layer)

The lakehouse-side, **human-driven** copilot: a fraud analyst investigates a flagged case
in natural language. A multi-tool agent (Mosaic AI Agent Framework) routes between three
tools. See `docs/copilot-design.md`.

> **What runs where.** The **tool implementations** and the **evaluation harness** are
> plain Python, **tested locally**. The agent's **LLM tool-routing**, **Vector Search**
> embedding, **Genie NL→SQL**, and the **LLM-judge** evaluation run only on Databricks and
> are **deferred**. Copilot **infra** (Vector Search index, agent serving endpoint, Genie
> space) is the next step.

## Tools (`tools/`)

| Tool | Use for | Implementation here | In production |
|---|---|---|---|
| `query_lakehouse` | precise structured facts (counts, sums, rates, history) | `LakehouseTools` — parameterized Spark queries over a transactions table (injected) | Genie NL→SQL over Unity Catalog |
| `search_similar_cases` | "cases like this one" — semantic similarity | `SimilarCaseSearch` — formats top-k from an **injectable** Vector index client | Vector Search over case embeddings |
| `get_fraud_score` | "why was this flagged" | `FraudScoreTool` — **reuses the `ml/serving` scorer contract** | the same Mosaic endpoint Bedrock uses |

Tool **descriptions are first-class** — routing depends on them. They live in `agent.py`.

## Agent (`agent.py`, `instructions/`)

`build_agent()` returns a declarative `CopilotAgent`: the three `ToolSpec`s (name +
description + JSON input schema) and the versioned routing system prompt
(`instructions/copilot_v1.md`): precise/structured → `query_lakehouse`; "similar / like
this" → `search_similar_cases`; "why flagged" → `get_fraud_score`. Answers are grounded in
tool output and cite their sources. Registering this with the Agent Framework + the live
LLM router is deferred.

## Evaluation (`eval/`)

- `dataset.py` — a labeled set of representative analyst questions → expected tool +
  answer shape (gates prompt / tool-description changes).
- `scoring.py` — a **local** harness computing tool-selection accuracy + a per-tool
  breakdown from a router (`question → tool`). `keyword_router` is a deterministic
  baseline to exercise the harness offline — **not** the production router.

The full Mosaic AI Agent Evaluation (LLM-judge correctness / groundedness / relevance)
runs on Databricks and is deferred.

## Local testing

Needs a JDK for the `query_lakehouse` Spark tests (see `pipelines/README.md`).

```bash
pytest tests/agents/databricks     # tools, agent registration, eval harness
ruff check .
```
