# FintelliGuard

Real-time financial fraud detection & compliance platform.
AWS Bedrock · Databricks Mosaic AI · Kafka/MSK · Spark · Terraform · LangGraph

## What this system does (mental model — keep this in mind every session)

A **three-tier decisioning funnel**:

1. **Tier 1 — XGBoost (Mosaic AI Model Serving):** scores EVERY transaction in <50ms. The transaction proceeds or stops on this score. ~99% pass and are done.
2. **Tier 2 — AWS Bedrock Agent:** only suspicious transactions (~1%, high score). Produces a compliance verdict + documented explanation via RAG over regulations, gated by Guardrails. This is the slow reasoning layer — NOT the fast scorer.
3. **Tier 3 — Databricks Mosaic AI copilot:** human fraud analysts investigate flagged cases asynchronously. NOT automated — a human-driven tool combining NL→SQL (Genie), semantic retrieval (Vector Search), and `get_fraud_score()`.

**Two-zone GenAI:** Bedrock owns the real-time external verdict (AWS edge, near the payment gateway). Databricks Mosaic AI owns the async internal copilot (where the lakehouse data lives). This mirrors a brownfield two-team enterprise; the two are connected by a contract, not coupling.

## Project structure

```
fintelliguard/
├── infra/
│   ├── aws/          # TF layer 1 — MSK, S3, Secrets Manager, API GW, IAM, KMS
│   ├── databricks/   # TF layer 2 — workspace + Unity Catalog
│   └── bundles/      # Databricks Asset Bundles — DLT pipelines + Model Serving
├── pipelines/
│   ├── bronze/ silver/ gold/   # DLT tables per medallion layer
├── ml/
│   ├── training/     # MLflow + XGBoost training (IEEE-CIS)
│   ├── features/     # Feature Store defs + adapters (adapter_stream.py, adapter_ieee.py)
│   ├── serving/      # Mosaic AI Model Serving endpoint config
│   ├── monitoring/   # Feature-drift detection (PSI + two-sample KS)
│   └── governance/   # Generates model/dataset cards + EU-AI-Act Annex IV doc from the code
├── agents/
│   ├── bedrock/      # Bedrock Agent + KB + Guardrails + action groups
│   │   ├── guardrails/  # Guardrail policy model + red-team set + coverage gate
│   │   └── eval/        # Verdict acceptance gate (schema·PII·grounding·faithfulness·decision)
│   ├── databricks/   # Mosaic copilot (Agent Framework) + Vector Search + Agent Evaluation
│   └── langgraph/    # Self-healing Supervisor + Medic
├── simulator/        # Python transaction generator (~500 txns/sec)
├── dashboards/       # Grafana dashboard JSON exports
├── tests/            # Unit + integration tests per layer (199, incl. the Responsible-AI gates)
└── docs/
    ├── governance/   # Generated regulated-AI docs (model/dataset cards, guardrail coverage, AI-Act)
    └── ...           # Architecture decisions, narrative, diagrams
```

## Stack

| Layer | Technology |
|---|---|
| Ingestion (streaming) | Kafka / AWS MSK → Spark Structured Streaming |
| Ingestion (batch) | S3 → Databricks Auto Loader |
| Data platform | Databricks DLT · Unity Catalog · Delta Lake |
| ML training | XGBoost · MLflow · Mosaic AI Feature Store |
| ML serving | Mosaic AI Model Serving (REST, autoscale) |
| Real-time AI (Tier 2) | AWS Bedrock Agent · Knowledge Bases · Guardrails · Claude (Haiku 4.5 → Sonnet 4.6) |
| Investigation AI (Tier 3) | Mosaic AI Agent Framework · Vector Search · Genie · Agent Evaluation |
| Self-healing | LangGraph Supervisor + Medic · LangSmith tracing |
| IaC | Terraform (3 isolated layers) · Databricks Asset Bundles |
| CI/CD | GitHub Actions |
| Observability | Grafana · Prometheus · LangSmith |

## Engineering rules — always follow

**IaC only.** Every cloud resource in Terraform or DABs. No console deployments, ever.

**Secrets never in code.** All credentials via AWS Secrets Manager or Databricks secret scopes. Never in `.tf` files, notebooks, env vars, or git.

**Terraform state isolated per layer.** `infra/aws`, `infra/databricks`, `infra/bundles` each own their remote state. Never reference state across layers — use outputs + data sources.

**DLT naming convention:** `bronze.<name>`, `silver.<name>`, `gold.<name>`. Unity Catalog: `fintelliguard.{bronze,silver,gold}`.

**Feature parity is non-negotiable.** The same 15 Gold features train the model AND serve at inference. Any feature change updates `ml/training/` and `ml/features/` (both adapters) in the SAME commit. See `@docs/features.md`.

**The cross-cloud contract.** Bedrock reaches the model ONLY via `get_fraud_score()` against the Mosaic Model Serving endpoint. Bedrock never reads Delta tables, never reads raw data, never knows Mosaic internals. The endpoint is reached through a private VPC endpoint, never public.

**Bedrock output is regulated.** Every Tier-2 verdict passes through Guardrails (PII redaction + hallucination guard) and is traced in LangSmith for audit. No verdict ships without grounding in the Knowledge Base regulatory text.

**Responsible-AI gates are deterministic and CI-enforced** (see `docs/governance/`). The guardrail is proven against a labelled red-team set (`agents/bedrock/guardrails/` — block-rate must stay 100%, and the coverage test parses `guardrail.tf`, so removing a policy class fails CI). Every verdict must pass the deterministic verdict gate (`agents/bedrock/eval/judge.py`: schema, no-PII, grounding, faithfulness, decision). The model/dataset cards + EU-AI-Act doc are **generated from the code** (`python -m ml.governance.generate`) — edit a threshold and run `make govern-docs`, or CI's `--check` fails. Feature drift is monitored by `ml/monitoring/drift.py` (PSI/KS).

**Copilot tool routing.** The copilot (Agent Framework) chooses between Genie (NL→SQL, precise facts), Vector Search (semantic similar-case retrieval), and `get_fraud_score()`. Tool descriptions are first-class engineering — write them carefully. Routing quality is measured by Mosaic AI Agent Evaluation.

**MLflow promotion policy.** Staging→Production only when AUC-ROC ≥ 0.92 AND fraud-class precision ≥ 0.85 on held-out test. Document metrics in the MLflow run before promoting.

**Done = runs + tested.** A task is not complete when code is generated — only when it runs end-to-end and has a passing test. Never mark a layer done on generated-but-unrun code.

## Cost controls — always active in development

- Databricks clusters: auto-terminate after 30 min idle (set in cluster config, not manually).
- AWS MSK: use local Kafka (Docker) in dev. Provision MSK only for integration testing and final demo.
- Bedrock: use `anthropic.claude-haiku-4-5` in dev. Switch to `anthropic.claude-sonnet-4-6` for final evaluation only.
- Always `terraform plan` before `apply`. Never `apply` without reviewing the plan.
- `terraform destroy` per layer when not actively working.

## Reference docs (load on demand with @)

- `@docs/NARRATIVE.md` — business context + architectural decisions
- `@docs/PROJECT_PLAN.md` — full master plan, roadmap, component inventory
- `@docs/data-flow.md` — sources → bronze → silver → gold → ML → Bedrock/copilot
- `@docs/features.md` — the 15 Gold features: definition, computation, type, source mapping
- `@docs/bedrock-integration.md` — Bedrock action group schema + Mosaic API contract
- `@docs/copilot-design.md` — Agent Framework tools, prompts, evaluation
