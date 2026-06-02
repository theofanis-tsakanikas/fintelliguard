# FintelliGuard

Real-time financial fraud detection & compliance platform.
*AWS Bedrock · Databricks Mosaic AI · Kafka/MSK · Spark · Terraform · LangGraph*

## Mental model

### Three-tier decisioning funnel

1. **Tier 1 — XGBoost (Mosaic AI Model Serving):** scores **every** transaction in
   <50 ms. The transaction proceeds or stops on this score. ~99% pass and are done.
2. **Tier 2 — AWS Bedrock Agent:** only suspicious transactions (~1%, high score).
   Produces a compliance verdict + documented explanation via RAG over regulations,
   gated by Guardrails. The slow reasoning layer — **not** the fast scorer.
3. **Tier 3 — Databricks Mosaic AI copilot:** human fraud analysts investigate flagged
   cases asynchronously. **Not** automated — NL→SQL (Genie), semantic retrieval
   (Vector Search), and `get_fraud_score()`.

### Two-zone GenAI

**Bedrock** owns the real-time external verdict (AWS edge, near the payment gateway).
**Databricks Mosaic AI** owns the async internal copilot (where the lakehouse data
lives). Two brownfield teams connected by a **contract, not coupling**: Bedrock reaches
the model only via `get_fraud_score()` against the Mosaic Model Serving endpoint.

## Stack

| Layer | Technology |
|---|---|
| Ingestion (streaming) | Kafka / AWS MSK → Spark Structured Streaming |
| Ingestion (batch) | S3 → Databricks Auto Loader |
| Data platform | Databricks DLT · Unity Catalog · Delta Lake |
| ML training | XGBoost · MLflow · Mosaic AI Feature Store |
| ML serving | Mosaic AI Model Serving (REST, autoscale) |
| Real-time AI (Tier 2) | AWS Bedrock Agent · Knowledge Bases · Guardrails · Claude (Haiku→Sonnet) |
| Investigation AI (Tier 3) | Mosaic AI Agent Framework · Vector Search · Genie · Agent Evaluation |
| Self-healing | LangGraph Supervisor + Medic · LangSmith tracing |
| IaC | Terraform (3 isolated layers) · Databricks Asset Bundles |
| CI/CD | GitHub Actions |
| Observability | Grafana · Prometheus · LangSmith |

## Repository layout

| Path | Purpose |
|---|---|
| `infra/` | Terraform (aws, databricks, bundles) — all cloud resources, IaC only |
| `pipelines/` | DLT medallion tables: bronze → silver → gold |
| `ml/` | XGBoost training, Feature Store adapters, Model Serving config |
| `agents/` | Bedrock (Tier 2), Databricks copilot (Tier 3), LangGraph self-healing |
| `simulator/` | ~500 txns/sec transaction generator |
| `dashboards/` | Grafana dashboard JSON exports |
| `tests/` | Unit + integration tests |
| `docs/` | Architecture decisions, narrative, contracts |

## Getting started

> _Placeholder — expanded as layers land._

```bash
# Python tooling
python3 -m venv .venv && source .venv/bin/activate
pip install ruff pytest

make fmt     # format
make lint    # ruff check
make test    # pytest

# One-time: provision the Terraform remote-state backend
cd infra/aws/bootstrap && terraform init && terraform apply
```

See `CLAUDE.md` for engineering rules and `docs/` for architecture.
