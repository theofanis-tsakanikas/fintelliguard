# FintelliGuard — Master Plan (v2, final architecture)

> Real-time financial fraud detection & compliance platform
> AWS Bedrock · Databricks Mosaic AI · Kafka · Spark · Terraform · LangGraph
>
> This is the definitive, complete version — three-tier decisioning, two-zone GenAI, full component depth.

---

## 1. The project in one paragraph

A system that ingests financial transactions in real time, scores them for fraud risk in <50ms, automatically produces a compliance explanation grounded in real regulations (AML, PSD2) for the suspicious ones, and gives human analysts an AI copilot to deeply investigate flagged cases. It solves fraud latency and compliance cost simultaneously.

---

## 2. Business problem

**Fraud latency.** Rule-based systems flag transactions after approval. At 10k txns/day with a 0.1% fraud rate → ~142,000 EUR daily exposure.

**Compliance cost.** AML/PSD2 require a documented justification per flagged transaction. A mid-size bank → 40–80 analysts dedicated to this alone.

---

## 3. Three-tier decisioning model (the core)

Every transaction passes through a three-level funnel. Each subsequent tier is more expensive and runs on a smaller volume.

| Tier | Who | What it does | Speed | Volume |
|---|---|---|---|---|
| **1** | XGBoost (Mosaic Model Serving) | Numeric fraud score → transaction proceeds or stops | <50ms | 100% |
| **2** | Bedrock Agent | Compliance verdict + documented explanation (RAG + Guardrails) | seconds | ~1% (suspicious) |
| **3** | Databricks Mosaic AI copilot | Human-driven deep investigation of flagged cases | minutes/hours, async | flagged |

**Critical:** the fast scoring is done by **XGBoost**, not Bedrock. Bedrock is the reasoning layer only for suspicious transactions. Databricks is present in **both** Tier 1 (serving) **and** Tier 3 (copilot).

---

## 4. Two-zone GenAI — why BOTH platforms

Brownfield narrative: the company has an existing Databricks investment (Data Platform team) and an AWS-native estate (Cloud team). We do not migrate — we build integration.

**Zone A — AWS Bedrock (real-time, external, AWS edge):**
The compliance verdict lives next to the payment gateway. Latency-critical, customer-facing, AWS security perimeter.

**Zone B — Databricks Mosaic AI (async, internal, lakehouse):**
The analyst copilot lives where the data lives. Exploratory, touches TBs of history, NL→SQL + vector retrieval.

**Why not forced:** the real-time verdict cannot live in Databricks (latency + payment edge); the copilot cannot live in Bedrock (data + volume of history). Each in its natural home.

**Honest stance (for interviews):** in a single-vendor shop it would all be on one platform. The split deliberately mirrors a realistic two-team enterprise and uses each cloud's native security model.

---

## 5. Data sources & flow

Two **orthogonal** paths — one feeds training, the other inference.

**Streaming (inference):** Python simulator (~500 txns/sec) → Kafka/MSK → Spark Structured Streaming → bronze → silver → gold.txn_features_realtime → Feature Store (online).

**Batch (training):** IEEE-CIS (590k labeled txns) → S3 → Auto Loader → bronze → silver → gold.txn_features_training → MLflow.

They converge only at the Model Serving endpoint. The same 15 features → eliminates training-serving skew.

---

## 6. The 15 Gold features

Full definition: see `docs/features.md`. Categories: Amount (3), Velocity (4), Identity/Device (3), Geography (2), Merchant (2), Temporal (1). Semantic features with a per-source adapter (stream / IEEE-CIS). State management designed as flatMapGroupsWithState + checkpointing (not built — Gold is a full-table recompute today; see docs/features.md). We do not use V1-339 (not explainable).

---

## 7. ML model

XGBoost/LightGBM on tabular data. MLflow experiment tracking + Model Registry. Promotion Staging→Production only with AUC-ROC ≥ 0.83 and fraud precision ≥ 0.85, documented in the MLflow run. Mosaic AI Model Serving (REST, autoscale, p50 <30ms). Feature importance returned with the score → feeds Bedrock reasoning.

---

## 8. Full component list (everything, in depth)

### Databricks (Data Platform)
- **DLT pipelines** — Bronze/Silver/Gold, exactly-once, checkpointing, expectations with quarantine
- **Unity Catalog** — RBAC, lineage, row-level security on Gold
- **Mosaic AI Feature Store** — online (<5ms serving) + offline (point-in-time training joins)
- **MLflow** — experiments, registry, promotion policy
- **Mosaic AI Model Serving** — XGBoost REST endpoint, autoscale
- **Vector Search** — embeddings of historical fraud cases for the copilot
- **Mosaic AI Agent Framework** — the conversational analyst copilot
- **Genie** — NL→SQL over the lakehouse
- **Mosaic AI Agent Evaluation** — quality/groundedness scoring of the copilot

### AWS Bedrock (Cloud Platform)
- **Bedrock Agent** — fraud/compliance verdict, tool use (`get_fraud_score()`)
- **Bedrock Knowledge Bases** — managed RAG over AML/PSD2/fraud patterns
- **Bedrock Guardrails** — PII redaction + hallucination guards on regulated output
- **Tiered models** — Claude Haiku (triage/dev) → Sonnet (final verdict)
- **Bedrock model evaluation** — verdict quality

### Shared infra / ingestion
- **Kafka / MSK** — streaming ingestion (local Docker in dev)
- **S3** — raw landing + offline feature store
- **API Gateway** — external fraud-verdict endpoint
- **Secrets Manager · KMS · IAM** — secrets, encryption, least-privilege
- **Private VPC endpoint** — Mosaic endpoint not public

### Orchestration & observability
- **LangGraph** — Supervisor + Medic self-healing (pipeline health, consumer lag, endpoint p99 → fallback model version)
- **LangSmith** — trace every Bedrock + copilot invocation (audit trail)
- **Grafana + Prometheus** — pipeline health, fraud score distribution, verdict latency

### IaC & CI/CD
- **Terraform** — 3 isolated layers (aws / databricks / bundles)
- **Databricks Asset Bundles** — DLT + Model Serving deployment
- **GitHub Actions** — PR validation, bootstrap, deploy, guarded destroy

---

## 9. Engineering principles (non-negotiable)

IaC always · Security by default (least-privilege, vaults, KMS, private networking) · Observability from day one · Self-healing over manual · Standards before speed.

---

## 10. Repository structure

```
fintelliguard/
├── CLAUDE.md  README.md
├── infra/        aws/ · databricks/ · bundles/
├── pipelines/    bronze/ · silver/ · gold/
├── ml/           training/ · features/ · serving/
├── agents/
│   ├── bedrock/      agent + knowledge-base + guardrails + action-groups
│   ├── databricks/   copilot (agent-framework) + vector-search + eval
│   └── langgraph/    self-healing supervisor + medic
├── simulator/    dashboards/   tests/
└── docs/         NARRATIVE · data-flow · features · bedrock-integration · copilot-design
```

---

## 11. Roadmap — 12 weeks (a phase closes only when it RUNS + is tested)

| Wk | Phase | "Done" = runs & tested |
|---|---|---|
| 1-2 | Foundation | TF: AWS infra + Databricks workspace + Unity Catalog. Reproducible <30 min. Smoke test. |
| 3-4 | Ingestion & Medallion | Simulator → Kafka → DLT Bronze/Silver/Gold (stream). IEEE-CIS batch. Expectations + quarantine work. |
| 5-6 | ML & Mosaic core | XGBoost training, MLflow, Feature Store (online+offline), Model Serving live. Latency measured. |
| 7-8 | AWS Bedrock zone | KB (AML/PSD2), Agent, Guardrails, `get_fraud_score()` cross-cloud call works end-to-end. |
| 9 | Databricks copilot zone | Vector Search + Agent Framework + Genie. Agent Evaluation runs. |
| 10 | Self-healing & integration | LangGraph Medic, two-tier flow end-to-end, three-tier funnel verified. |
| 11 | Observability & hardening | Grafana dashboards, LangSmith traces, load test, security review. |
| 12 | Docs & polish | README, diagrams, cost analysis, latency benchmarks, blog post. |

---

## 12. Cost (development)

~125-215 EUR/month. Controls: local Kafka in dev, Bedrock Haiku in dev, cluster auto-terminate 30 min, always `terraform plan` before `apply`, `destroy` per layer when idle. Managed well: <250 EUR total.

---

## 13. Interview talking points

- **"Why two AI platforms?"** → brownfield two-team enterprise; native security model per cloud; integration without coupling (contract, not coupling).
- **"Why not everything in Databricks?"** → in single-vendor I would; deliberately split for realism.
- **"Why XGBoost and not an LLM for scoring?"** → tabular data, <50ms, explainable, industry standard. LLM for tabular = overengineering.
- **"How do you avoid training-serving skew?"** → Feature Store, same 15 features, point-in-time joins.
- **"How do you ensure regulated output?"** → Guardrails (PII + hallucination), LangSmith audit trail, grounded RAG on real regulatory text.
