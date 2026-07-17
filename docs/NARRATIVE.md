# FintelliGuard — Architectural Narrative
### AWS Bedrock · Databricks Mosaic AI · Kafka · Spark · Terraform · LangGraph

---

## The problem this platform solves

Every financial institution processing card transactions faces two simultaneous problems that traditional approaches handle separately and poorly.

**Fraud detection latency.** Rule-based systems flag transactions after they are approved. By the time a human analyst opens a case, the fraudster has moved on. At 10,000 transactions per day and a 0.1% fraud rate, even modest exposure compounds into six-figure annual losses.

**Compliance cost.** AML and PSD2 regulations require that every flagged transaction carry a documented reasoning chain referencing specific regulatory articles. Today this is manual work — a mid-size bank dedicates 40–80 analysts to it.

FintelliGuard addresses both in one system: it scores every transaction in under 50ms, produces a regulation-grounded explanation for the suspicious ones, and gives human analysts an AI copilot to investigate flagged cases in depth.

---

## How a transaction flows — the three-tier model

Not every transaction needs the same scrutiny. FintelliGuard uses a three-tier funnel, where each tier is more expensive and runs on a smaller slice.

**Tier 1 — XGBoost scoring (every transaction, <50ms).** The XGBoost model, served on Databricks Mosaic AI Model Serving, scores every transaction. ~99% score low and are approved instantly. This is the fast, cheap, numeric decision.

**Tier 2 — Bedrock compliance verdict (suspicious only, ~1%).** Transactions above the risk threshold go to an AWS Bedrock Agent. It calls back to the model for the score and feature importance, performs RAG over the regulatory knowledge base, passes the output through Guardrails, and produces a documented compliance verdict. This is the slow reasoning layer — it never does the fast scoring.

**Tier 3 — Analyst copilot (flagged cases, human-driven, async).** Cases the verdict flags for human review land in the lakehouse. A fraud analyst later investigates each using the Databricks Mosaic AI copilot — not an automated tier, but a human decision-support tool.

The key distinction: Tiers 1 and 2 are automated and live in the real-time transaction path. Tier 3 is human and asynchronous.

---

## Why two AI platforms — not one

This is the question every senior engineer asks. The honest answer is not technical necessity — either platform could, in principle, do most of the other's job. The answer is organizational reality.

In every financial institution above a certain scale, the Data Platform team and the Cloud Infrastructure team are separate departments with separate budgets and vendor contracts. The Data Platform team owns Databricks — the lakehouse, the fraud model, the Feature Store. The Cloud Infrastructure team owns the AWS estate where customer-facing APIs live, and evaluates Bedrock because it integrates natively with their existing AWS security controls.

FintelliGuard mirrors this brownfield reality with one clean decision: **Databricks Mosaic AI exposes the fraud model as a versioned REST endpoint; AWS Bedrock treats that endpoint as an opaque Tool.** The two systems know nothing of each other's internals. The boundary is a contract, not a coupling — which is exactly what makes platform migrations survivable.

This split also places each GenAI workload in its natural home:

- **AWS Bedrock — real-time, external.** The compliance verdict fires synchronously at the payment edge, inside the AWS security perimeter. It cannot live in Databricks: it is latency-critical and sits where customer transactions enter.
- **Databricks Mosaic AI — async, internal.** The analyst copilot explores terabytes of historical data in the lakehouse via NL→SQL and semantic retrieval. It cannot live in Bedrock: the data lives in the lakehouse and the work is exploratory.

Neither is forced. Each is where it belongs.

---

## Architecture decisions

**Mosaic AI owns the score; Bedrock owns the reasoning.** Detection and explanation are different jobs. XGBoost answers "how likely is this fraud?" with a number. Bedrock answers "why, which regulation, and what to do?" with a documented narrative. A number is not a compliance decision; the model's feature importance is the *input* to Bedrock's reasoning, not a substitute for it.

**The Feature Store is the single source of truth for features.** The same 15 Gold features train the model (offline store) and serve it at inference (online store, <5ms). This eliminates training-serving skew — one of the most common silent production failures.

**Guardrails on all regulated output.** Every Tier-2 verdict passes through Bedrock Guardrails for PII redaction and hallucination control, and is traced end-to-end in LangSmith. In a regulated context, proving the model did not leak PII or invent regulations is a requirement, not a nice-to-have.

**Self-healing at the pipeline level.** LangGraph monitors pipeline health, consumer lag, and endpoint latency, falling back to the previous model version when serving degrades. The reasoning agents are consumers, not operators.

**Everything is infrastructure as code.** Three Terraform layers with isolated remote state, plus Databricks Asset Bundles. No resource is created through a console.

---

## What this platform is, and is not

It is not a tutorial follow-along: there is no reference implementation for a Bedrock Agent calling a Mosaic AI Model Serving endpoint as a Tool, and that integration was designed from first principles. Least-privilege IAM, vaulted secrets, KMS encryption and quarantine-not-drop DLT routing are real and tested.

It is also **not deployed**, and several things this document once claimed as accomplished were not. The honest scope is in [`README.md`](../README.md#project-status); the short version:

- **Gold is a full-table recompute**, not `flatMapGroupsWithState` streaming state. The stream adapter is pure and windowed; the stateful streaming execution described in [`features.md`](features.md) is a design, not code.
- **The Feature Store online path is a spec**, not a lookup. There is no <5ms online store.
- **LangSmith traces the self-healing graph only** — not the Bedrock verdict path and not the copilot, which are the two paths that produce regulated output.
- **PrivateLink is ready, not on.** The VPC endpoint is `count = 0` unless a service name is supplied.
- **The Tier-2 reasoner is stubbed locally.** Everything that *judges* a verdict — the acceptance gate, the guardrail — is the shipping code.

That list exists because the gap between what this repository said and what it did was, for a while, its most serious defect. A guardrail was declared in Terraform and never attached to the agent; a feature was documented as a lookup and served as the constant 0.0; a data-quality metric read 100% because the rows that could fail it had already been filtered out. All three were green in CI. The controls are real now, and `make gate-attack` breaks each one on purpose so you can watch it refuse — because a claim you can attack is worth more than a claim you can only read.

---

## Quantified business impact

Conservative estimates based on published industry benchmarks, included to frame the engineering investment for a business stakeholder.

| Metric | Baseline | With FintelliGuard | Basis |
|---|---|---|---|
| Fraud detection latency | 2–4 h (batch) | < 50 ms | Kafka + Mosaic serving |
| Compliance review per case | 45 min | 3 min | Bedrock verdict + RAG |
| False positive rate | ~8% | ~3% | MLflow evaluation |
| Daily fraud exposure (10k txns) | ~142,000 EUR | ~53,000 EUR | 63% reduction at 0.1% |
| Compliance analyst FTE | 40–80 | 10–20 (oversight) | 75% drafting automation |

*Numbers reflect synthetic data and industry benchmarks; they demonstrate the mechanism, not measured production results.*
