<p align="center">
  <img src="docs/assets/banner.png" alt="FintelliGuard — real-time fraud detection & compliance" width="100%">
</p>

# FintelliGuard

<p align="center">
  <a href="https://github.com/theofanis-tsakanikas/fintelliguard/actions/workflows/ci.yml"><img src="https://github.com/theofanis-tsakanikas/fintelliguard/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/IaC-Terraform-7B42BC?logo=terraform&logoColor=white" alt="Terraform">
  <br>
  <img src="https://img.shields.io/badge/AWS-Bedrock-FF9900?logo=amazonwebservices&logoColor=white" alt="AWS Bedrock">
  <img src="https://img.shields.io/badge/Databricks-Mosaic%20AI-FF3621?logo=databricks&logoColor=white" alt="Databricks Mosaic AI">
  <img src="https://img.shields.io/badge/Kafka-MSK-231F20?logo=apachekafka&logoColor=white" alt="Kafka / MSK">
  <img src="https://img.shields.io/badge/XGBoost-scorer-006ACC" alt="XGBoost">
  <img src="https://img.shields.io/badge/MLflow-2.16-0194E2?logo=mlflow&logoColor=white" alt="MLflow">
  <br>
  <img src="https://img.shields.io/badge/fraud%20AUC--ROC-0.87-2ea44f" alt="fraud AUC-ROC 0.87">
  <img src="https://img.shields.io/badge/guardrail%20red--team-100%25-2ea44f" alt="guardrail red-team 100%">
  <img src="https://img.shields.io/badge/tests-430%2B%20passing-2ea44f" alt="430+ tests passing">
</p>

**Real-time financial fraud detection & compliance platform.**
*AWS Bedrock · Databricks Mosaic AI · Kafka / MSK · Spark · XGBoost · Terraform · GitHub Actions*

> **The name** — **Fin**(ancial) + **Intelli**(gence) + **Guard**: it *guards* financial
> transactions *intelligently*. The "Guard" is literal — Bedrock **Guardrails** are a
> first-class, CI-enforced control in Tier 2.

---

## Contents

- [The problem](#the-problem) · [Status — proven live, then torn down](#status--proven-end-to-end-on-real-cloud-then-torn-down)
- [Architecture — three tiers, two clouds](#architecture--three-tier-decisioning-two-zone-genai)
  - [The decisioning funnel](#the-decisioning-funnel) · [Tier 1 · scorer](#tier-1--the-scorer-xgboost-on-mosaic-ai-model-serving) · [Tier 2 · compliance agent](#tier-2--the-compliance-agent-aws-bedrock) · [Tier 3 · analyst copilot](#tier-3--the-analyst-copilot-databricks-mosaic-ai)
- [Data flow — the medallion pipeline](#data-flow--the-medallion-pipeline-dlt)
- [The private cross-cloud path (MSK ↔ Databricks)](#the-private-cross-cloud-path-msk--databricks)
- [CI/CD & IaC](#cicd--iac--everything-is-gated-nothing-is-by-console) · [Observability](#observability) · [Regulated-AI docs](#regulated-ai-generated-from-the-code)
- [Testing philosophy](#testing-philosophy-honest) · [Repository layout](#repository-layout) · [Beyond the demo](#beyond-the-demo) · [Docs](#docs)

---

## The problem

Card-fraud systems fail on two fronts at once. Rule engines flag transactions *after*
approval, so losses are booked before an analyst opens the case. And AML / PSD2 require a
**documented, regulation-grounded justification for every flagged transaction** — work a
mid-size bank throws 40–80 analysts at.

FintelliGuard scores **every** transaction in under 50 ms, auto-drafts a
regulation-grounded compliance verdict for the suspicious ~1%, and gives analysts an AI
copilot to investigate the rest — attacking fraud latency and compliance cost in one system.

## Status — proven end-to-end on real cloud, then torn down

This is not a "runs on my laptop" repo. The whole estate was **provisioned on real AWS +
Databricks through CI, exercised end-to-end, captured, and then destroyed to zero cost**
(the teardown is a first-class, guarded workflow). Everything below is backed by a real run;
the screenshots are from it. Bringing it back up is one `Deploy` dispatch.

<p align="center">
  <img src="docs/assets/ci_deploy_summarry.png" width="820" alt="Deploy #85 — Success in 1h37m, secret scan clean">
</p>

> One `workflow_dispatch` provisioned three Terraform layers + Databricks Asset Bundles,
> built the medallion tables, trained + gated + registered the model, stood up the serving
> endpoints, wired the cross-cloud contract, and served the Tier-2 agent and Tier-3 copilot —
> in **1h 37m**, gates green, **no secrets leaked**.

---

## Architecture — three-tier decisioning, two-zone GenAI

A transaction flows through a funnel where each tier is more expensive and runs on a smaller
slice. The two GenAI zones live in their natural homes and are joined by a **contract, not
coupling**: Bedrock reaches the model *only* through `get_fraud_score()`, over private
connectivity, never public.

```mermaid
flowchart LR
  SIM["Simulator<br/>~30–500 txns/s"] --> K["Kafka / MSK<br/>topic: txn.raw"]
  K --> P["DLT medallion<br/>bronze → silver → gold<br/>14 features"]

  subgraph LAKE["Databricks lakehouse — Mosaic AI zone (internal)"]
    direction TB
    P --> FS[("Feature Store<br/>lookup by card_hash")]
    FS --> T1["<b>Tier 1 · XGBoost</b><br/>Mosaic Model Serving<br/>&lt;50 ms · every txn"]
    T3["<b>Tier 3 · Analyst copilot</b><br/>Vector Search · get_fraud_score<br/>(Genie deferred) · human, async"]
  end

  T1 -->|"~99% low score"| OK["Approved"]
  T1 -->|"~1% suspicious"| T2

  subgraph EDGE["AWS edge — Bedrock zone (real-time, external)"]
    T2["<b>Tier 2 · Bedrock Agent</b><br/>compliance verdict<br/>RAG (AML/PSD2) + Guardrails"]
  end

  T2 -. "get_fraud_score() · private cross-cloud contract" .-> T1
  T2 -->|"flagged case"| T3
```

- **Tier 1 — XGBoost on Mosaic AI Model Serving.** Scores 100% of transactions in <50 ms;
  ~99% pass and are done. The fast, numeric decision, with per-prediction **TreeSHAP**
  explanations.
- **Tier 2 — AWS Bedrock Agent.** Only the suspicious ~1%. Calls back for the score via
  `get_fraud_score()`, grounds a compliance verdict in AML / PSD2 via RAG, and passes it
  through **Guardrails**. The slow reasoning layer — never the scorer.
- **Tier 3 — Databricks Mosaic AI copilot.** Human analysts investigate flagged cases
  asynchronously with semantic case retrieval (Vector Search) and the same
  `get_fraud_score()`. Decision support, not automation.

See [`docs/NARRATIVE.md`](docs/NARRATIVE.md) for the *why* and
[`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the full component inventory.

---

## The decisioning funnel

```mermaid
flowchart TD
  A["Transaction"] --> B["Tier 1 · get_fraud_score()<br/>XGBoost + TreeSHAP"]
  B --> C{"decision_hint"}
  C -->|"score &lt; 0.70 · allow"| D["Approved — done (~99%)"]
  C -->|"0.70–0.90 · review"| E["Auto-escalate"]
  C -->|"&ge; 0.90 · block"| E
  E --> F["Tier 2 · Bedrock verdict<br/>RAG over AML/PSD2"]
  F --> G["Verdict-acceptance gate<br/>schema · no-PII · grounding · faithfulness · direction"]
  G --> H["Output Guardrail<br/>PII redaction · grounding"]
  H --> I["Decision record (Art. 12)<br/>+ analyst copilot (Tier 3)"]
```

The orchestrator is `ml/serving/funnel.py`: it scores at Tier 1 and, only when
`decision_hint ∈ {review, block}`, auto-escalates (no human) to the Tier-2 agent. `allow`
clears. Proven live against the real Mosaic endpoint **and** the real Bedrock agent:

| Fraud transaction → escalates | Legit transaction → clears |
|---|---|
| <img src="docs/assets/cli_review_transaction.png" alt="fraud txn escalates to Tier 2"> | <img src="docs/assets/cli_allow_transaction.png" alt="legit txn cleared at Tier 1"> |

`fraud_score = 0.8875 → REVIEW → auto-escalate → documented Tier-2 verdict citing
**AMLD5 Art. 12 / 18a** and **PSD2 Art. 4**`, versus `fraud_score = 0.0044 → ALLOW → cleared
at Tier 1, no escalation`.

---

## Tier 1 — the scorer (XGBoost on Mosaic AI Model Serving)

Trained on **IEEE-CIS** (590,540 transactions, 20,663 fraud = **3.50%**) with a compact,
**interpretable 14-feature** contract — not the anonymised `V1–V339`, so every feature is
explainable to a compliance analyst and computable online.

**Promotion policy (enforced in `ml/training/promote.py`):** Staging → Production only when
**AUC-ROC ≥ 0.83 AND fraud-class precision ≥ 0.85** on held-out test. Fail-closed. The live
run cleared it and registered `fintelliguard.ml.fraud_scorer` → alias `production`:

<p align="center">
  <img src="docs/assets/train_dbx_run.png" width="760" alt="promotion gate PASS: AUC 0.8661, precision 0.8699">
</p>

> `promotion gate: PASS — AUC-ROC 0.8661 ≥ 0.83 AND fraud precision 0.8699 ≥ 0.85`

<table>
<tr>
<td><img src="docs/assets/fraud_experiment2.png" alt="MLflow run metrics + 14 params"></td>
<td><img src="docs/assets/score_endpoint.png" alt="serving endpoint fintelliguard-fraud-score Ready"></td>
</tr>
</table>

The scorer (`ml/serving/scorer.py`) bands the score — `≥ 0.90 → block`, `≥ 0.70 → review`,
else `allow` — and returns the exact per-transaction **TreeSHAP** contributions
(`pred_contribs`) as `top_features`: *why this transaction*, not global importance. The
serving endpoint `fintelliguard-fraud-score` is `Small` / scale-to-zero.

> The 14-feature contract is documented in [`docs/features.md`](docs/features.md); the
> canonical count is `len(FEATURE_SPECS)` in `ml/features/schema.py`. (A 15th,
> `merchant_risk_score`, was **removed, not faked** — it needs merchant identity *and* labels
> in one dataset, which no source here has. Some prose still says "15"; the executable count
> is **14**.)

---

## Tier 2 — the compliance agent (AWS Bedrock)

Only the flagged ~1% reach it. The agent `fintelliguard-dev-fraud-investigator` (alias
`live`) calls back for the score via its `FraudScoring` action group, grounds a verdict in a
**Knowledge Base of verbatim EUR-Lex regulation** (AMLD5, GDPR, PSD2, RTS-SCA), and every
verdict passes an output **Guardrail**.

<p align="center">
  <img src="docs/assets/bedrock_test.png" width="100%" alt="Bedrock agent verdict — reasoning grounded in AML/PSD2, recommended_action review">
</p>

The regulatory corpus lives in S3, is indexed into the Knowledge Base, and every verdict is
grounded in it — cited by source:

<table>
<tr>
<td><img src="docs/assets/s3_files.png" alt="Regulatory corpus in S3 — AMLD5 / GDPR / PSD2 / RTS-SCA"></td>
<td><img src="docs/assets/bedrock_kb.png" alt="Knowledge Base — 4 regulatory docs indexed, synced"></td>
</tr>
</table>

<p align="center">
  <img src="docs/assets/bedrock_test_sources.png" width="100%" alt="Pre-processing trace citing the exact KB source (s3://…/amld_2015_849…) — grounding proof">
</p>

- **Knowledge Base** `fintelliguard-dev-regulations` → vector store on **OpenSearch
  Serverless** (`fintelliguard-reg`), embeddings `amazon.titan-embed-text-v2:0`, four
  regulation documents indexed and synced.
- **Foundation model:** the wired default is **Amazon Nova Lite**
  (`eu.amazon.nova-lite-v1:0`) — switchable to **Claude Haiku 4.5**
  (`eu.anthropic.claude-haiku-4-5-20251001-v1:0`) once the account's one-time Bedrock
  Anthropic use-case approval is granted (streaming Anthropic models fail without it). Sonnet
  is design-spec only.
- **The cross-cloud contract** is a VPC-internal Lambda on a least-privilege role: it fetches
  online features, calls the Mosaic serving endpoint over **private connectivity** with a
  Databricks OAuth token pulled from **Secrets Manager** at runtime, and returns
  `{fraud_score, model_version, threshold, decision_hint, top_features}`. Bedrock never reads
  Delta, never sees raw data.

### Guardrails — proven to block, not just configured

The guardrail `fintelliguard-dev-guardrail` (bound to the agent at an **immutable version**)
enforces PII redaction (`CREDIT_DEBIT_CARD_NUMBER`, `NAME`, `EMAIL` → `ANONYMIZE`), a denied
topic (`investment-advice`), a prompt-attack filter, and **contextual grounding** at
threshold `0.75`. Tested live via `ApplyGuardrail`:

<p align="center">
  <img src="docs/assets/guardrails3.png" width="860" alt="Guardrail masks NAME / EMAIL / CARD in a verdict">
</p>

And proven deterministically in CI against a **labelled red-team set** (25 cases = 19
adversarial + 6 benign controls), which must stay at a **100% block rate with zero false
positives**:

<p align="center">
  <img src="docs/assets/cli_guardrails_evaluate.png" width="720" alt="guardrail red-team: 19/19 adversarial blocked, 0/6 benign, RESULT PASS">
</p>

> **Stated plainly:** that offline model is a *signature stand-in* for Bedrock's classifier —
> its block rate is a **regression score, not a measured safety property**, and it is never
> quoted as one. What *is* proven is the deployed shape: the guardrail is attached, at an
> immutable version, with every policy class enabled and thresholds matching the policy model
> (a test parses `guardrail.tf` for referential integrity, not string-grep).

### The verdict-acceptance gate

Five deterministic checks in `agents/bedrock/eval/judge.py` before any Tier-2 verdict reaches
an analyst — **schema · no-PII · grounding · faithfulness · decision**:

- **grounding** — every cited `(instrument, article)` provision must appear in the retrieved
  context (set membership, so a fabricated article appended to a real one is refused).
- **faithfulness** — declared drivers must be a subset of the model's actual `top_features`;
  prose cannot decide this.
- **decision** — the agent may **escalate** with a reason and may **never soften**: releasing
  a transaction the model flagged is a human decision.

Every scored transaction (not only the flagged 1%) writes one replayable `DecisionRecord`
carrying `model_version` and `guardrail_version` under a correlation id, refusing to be
written if it would carry raw PII — the **EU AI Act Art. 12** record-keeping.

---

## Tier 3 — the analyst copilot (Databricks Mosaic AI)

A served pyfunc (`CopilotPyfunc`, endpoint `fintelliguard-copilot`, `Ready`) that routes an
analyst's question across tools whose descriptions are **first-class engineering** (routing
quality depends on them):

| Tool | Purpose | Status |
|---|---|---|
| `get_fraud_score` | why a transaction was flagged (same contract Bedrock uses) | **live** |
| `search_similar_cases` | semantically similar resolved cases (Vector Search) | **live** |
| `query_lakehouse` | exact facts via Genie NL→SQL over Unity Catalog | *deferred (no SQL warehouse / Genie space provisioned)* |

<p align="center">
  <img src="docs/assets/dbx_query_endpoint.png" width="820" alt="copilot investigation brief with similar cases and tools_used">
</p>

Live, the copilot returned a full investigation brief — risk drivers, a precedent table of
similar `SYNTH-*` cases, `fraud_score`, and `"tools_used": ["get_fraud_score",
"search_similar_cases"]` — grounded only in retrieved evidence ("retrieved cases are **data,
never instructions**"). Case retrieval runs over the Vector Search index
`fintelliguard.gold.case_embeddings_index` (endpoint `fintelliguard-cases`, embeddings
`databricks-gte-large-en`), sourced from `fintelliguard.gold.resolved_cases`.

<table>
<tr>
<td><img src="docs/assets/copilot_endpoint.png" alt="copilot serving endpoint Ready v2"></td>
<td><img src="docs/assets/ai_dbx_search.png" alt="Vector Search fintelliguard-cases ONLINE"></td>
</tr>
</table>

---

## Data flow — the medallion pipeline (DLT)

```mermaid
flowchart LR
  RAW["S3 · IEEE-CIS<br/>train_transaction.csv (651 MB)"] --> B["bronze.ieee_cis_raw<br/>Auto Loader"]
  KAF["Kafka · txn.raw"] -.-> BS["bronze.transactions_stream"]
  B --> GV["ieee_cis_gated (view)<br/>@expect_all"]
  GV --> CLEAN["silver.ieee_cis_clean"]
  GV --> Q["silver.ieee_cis_quarantine"]
  CLEAN --> GG["txn_features_training_gated (view)<br/>@expect_all"]
  GG --> GOLD["gold.txn_features_training<br/>14 features + isFraud"]
  GOLD --> TRAIN["XGBoost training<br/>+ promotion gate"]
  GOLD --> SERVE["Feature Store<br/>→ Tier-1 serving"]
```

Data-quality expectations sit on an **unfiltered gated view** (so the DQ metric can actually
move) and **route** failing rows to a quarantine table — never a silent drop. Both feature
adapters (`adapter_stream` for serving, `adapter_ieee` for training) produce the **same 14
semantic features**, proven by a distributional parity test. The live run built 591K rows
clean:

<p align="center">
  <img src="docs/assets/medallion1.png" width="860" alt="DLT medallion graph — 591K records, expectations defined">
</p>

---

## The private cross-cloud path (MSK ↔ Databricks)

The differentiator: Databricks **classic compute already lives in the same customer-managed
VPC** as MSK, so the connection is a security-group rule and an IAM instance profile — **no
NCC / PrivateLink** (that is for serverless compute in Databricks' account). In-VPC routing
resolves the multi-broker advertised listeners natively.

```mermaid
flowchart LR
  subgraph VPC["Customer-managed VPC (private subnets)"]
    direction LR
    GEN["ECS Fargate<br/>generator (simulator)"] -->|"9098 · IAM SASL"| MSK[("Amazon MSK<br/>topic: txn.raw")]
    MSK -->|"9098 · IAM SASL"| SCO["ECS Fargate<br/>scorer (stream_service)"]
    MSK <-->|"9098 · SG rule"| DBX["Databricks<br/>classic compute"]
    LAM["Bedrock action-group<br/>Lambda"] -->|"private"| VPE(["VPC endpoints<br/>Secrets Mgr · KMS"])
  end
  DBX -. "OAuth token from Secrets Manager" .-> LAM
```

MSK IAM auth (`SASL_SSL` / `OAUTHBEARER`) has a subtlety the code handles: the token is only
served during `poll()`, so the client must **warm up** (poll first, tolerate the mid-handshake
`_TRANSPORT` raise) before producing. `ml/serving/msk_probe.py` proves the path with a real
Kafka round-trip from a Databricks cluster — produce a uniquely-marked record, read it back at
the exact `(partition, offset)`, raise loudly with SG / instance-profile / PassRole
diagnostics if it fails. It passed live:

<p align="center">
  <img src="docs/assets/streaming_dbx_run.png" width="100%" alt="MSK probe: round-tripped a marked record over the private path — PASS">
</p>

<p align="center">
  <img src="docs/assets/cloudwatch.png" width="100%" alt="scorer CloudWatch logs: live scored decisions — score → review / block">
</p>

> The in-VPC ECS scorer scores with a **bundled demo model** (so the live-stream demo runs
> before any model exists); the *real* Mosaic endpoint + *real* Bedrock verdict are exercised
> by `ml/serving/funnel.py`. The streaming stage is deliberately **run-then-destroyed**, not a
> standing service.

---

## CI/CD & IaC — everything is gated, nothing is by console

Three isolated Terraform layers (`infra/aws` → `infra/databricks` → `agents/bedrock/terraform`)
plus Databricks Asset Bundles, each with **per-layer remote state** in an S3 backend +
DynamoDB lock. Cross-layer references are **outputs → data sources**, never direct state reads.
OIDC in every cloud workflow (no static keys).

**`CI` (`ci.yml`)** runs on every PR — a reviewer can run the whole thing on a laptop:

<p align="center">
  <img src="docs/assets/ci_deploy_validate.png" width="860" alt="CI gates: gitleaks, ruff, pytest, Responsible-AI, gate-proof, checkov, terraform validate">
</p>

`gitleaks` → `ruff` → **pytest (full suite)** → **Responsible-AI gates** (guardrail red-team +
docs-in-sync) → **gate-proof (attack our own gates)** → `terraform fmt` → `checkov` →
`terraform validate` (per layer, offline) → `databricks bundle validate`.

**`Deploy` (`deploy.yml`, `workflow_dispatch`)** re-runs CI on the deploying ref, plans the
foundation layer behind an **environment approval gate**, then applies every layer in
dependency order. It is parameterised:

| Input | Options | Meaning |
|---|---|---|
| `layers` | `core` / `full` | `core` = Tier 1/3; `full` adds Tier-2 Bedrock + OpenSearch |
| `retrain` | `auto` / `force` | `auto` skips training if a `production` model exists (content-fingerprint) |
| `stage` | `all` / `network` / `train` / `serving` / `streaming` | staged rollout; `streaming` is deliberately **not** part of `all` |

<p align="center">
  <img src="docs/assets/ci_deploy_apply_all.png" width="860" alt="Deploy apply — every layer in order: KB load, DLT, train+gate+register, serving, cross-cloud wiring, copilot">
</p>

**`Destroy` (`destroy.yml`)** is guarded by a **typed confirmation** and tears down in reverse
order, stopping any running generator task first to free the ENI, emptying versioned buckets
before their layer, and failing loudly if any layer survives. The state backend
(`infra/aws/bootstrap`, OIDC role `fintelliguard-github-deploy`) is intentionally preserved.

> **Every gate is attacked in CI.** `make gate-proof` copies the repo, plants a *real*
> violation (detach the guardrail, restore an off-by-one, soften a verdict, pre-filter the DQ
> rows) and fails unless the real gate refuses it **for the right reason** — because this suite
> was once green while the guardrail was unattached, `merchant_risk_score` was a constant
> `0.0`, and the DQ metric read 100% on pre-filtered rows. Each attack is a bug this repo
> actually shipped.

---

## Observability

`make e2e` strings the real components — simulator → Kafka → scorer (real XGBoost + TreeSHAP +
the real verdict gate + guardrail) → Prometheus → Grafana — into a **running local funnel** on
`localhost:3000`, emitting the exact metric series the dashboards query. Five provisioned
Grafana dashboards; the local funnel lights up fully:

<p align="center">
  <img src="docs/assets/grafana1.png" width="860" alt="Grafana local funnel: throughput by decision, fraud-score distribution, verdict-gate, flagged 3.93%, guardrail blocks 0">
</p>

Flagged rate **3.93%**, verdict-gate accepting, **guardrail blocks 0** (clean verdicts, the
healthy state), decision-log refusals 0. Metric series: `fintelliguard_fraud_score`,
`fintelliguard_verdict_gate`, `fintelliguard_guardrail_blocks`, `fintelliguard_quarantined`,
`fintelliguard_decision_log_refusals`, plus `model_serving_*`.

---

## Regulated-AI, generated from the code

The **model card**, **dataset card**, an **EU AI Act Annex IV** technical document, and the
**guardrail coverage** report are *rendered from the actual code* (`make govern-docs` →
`python -m ml.governance.generate`) — the feature list, promotion thresholds, scorer bands,
drift thresholds, guardrail policy, and verdict-gate fields all pulled from source, so CI's
`--check` fails if the committed docs drift. See [`docs/governance/`](docs/governance/README.md).

Feature drift is monitored by `ml/monitoring/drift.py` (PSI + two-sample KS, NumPy-only, bands
0.10 / 0.25). *Scope, stated plainly: a library and a threshold — no scheduled job computes it
yet.*

---

## Testing philosophy (honest)

**71 test files, ~436 test functions.** Pure logic is unit-tested locally **with the real
engines** — local PySpark for the DLT transforms, real XGBoost + MLflow for training/serving,
real LangGraph for self-healing, mocked-client bridges for the Bedrock Lambda and Vector
Search. Infrastructure is **offline-validated** (`terraform validate` per layer, `databricks
bundle validate`, `checkov`) with no cloud calls.

And **the tests are themselves tested**: `make gate-proof` breaks each control on purpose and
demands the real gate refuse it, with three rules that keep it a proof rather than a ritual —
every gate must be **green first**, a **non-zero exit is not evidence** (the *named* test must
report the failure), and a mutation whose target has moved is reported **STALE**, not passed.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

make test            # pytest — full suite (local Spark + XGBoost + MLflow + LangGraph)
make lint            # ruff check + format check
make gate-proof      # break every control on purpose; each must be refused, for the right reason
make guardrail-scan  # guardrail red-team coverage gate
make iac-scan        # checkov over the Terraform layers
make govern-docs     # regenerate model/dataset cards + AI-Act doc from code

make e2e             # local end-to-end funnel → Grafana at http://localhost:3000 (admin/admin)
make e2e-down        # stop + clean up
```

Requires **Python 3.11+**, **Java 17** (PySpark tests), and the OpenMP runtime for XGBoost
(`brew install libomp` on macOS; bundled on Linux).

---

## Repository layout

| Path | Purpose |
|---|---|
| [`infra/aws/`](infra/aws/) | TF layer 1 — VPC, KMS, S3, Secrets, IAM, MSK (cost-guarded), ECS streaming; `bootstrap/` = state backend + OIDC |
| [`infra/databricks/`](infra/databricks/) | TF layer 2 — workspace + Unity Catalog (customer-managed VPC) |
| [`infra/bundles/`](infra/bundles/) | Databricks Asset Bundles — DLT, training, serving, Vector Search, copilot, streaming probe |
| [`pipelines/`](pipelines/) | DLT bronze → silver → gold (testable transforms + thin `@dlt` layer) |
| [`ml/features/`](ml/features/) | Canonical 14-feature schema + stream/IEEE adapters (parity-proven) |
| [`ml/training/`](ml/training/) | XGBoost + MLflow + promotion gate (**AUC ≥ 0.83 ∧ precision ≥ 0.85**) |
| [`ml/serving/`](ml/serving/) | `get_fraud_score()` scorer (TreeSHAP), funnel orchestrator, MSK probe, local stream service |
| [`ml/monitoring/`](ml/monitoring/) | Feature-drift detection (PSI + two-sample KS), NumPy-only |
| [`ml/governance/`](ml/governance/) | Generates model/dataset cards + EU AI Act Annex IV doc from the code |
| [`agents/bedrock/`](agents/bedrock/) | Tier-2: agent/KB/guardrail Terraform + action-group Lambda |
| [`agents/bedrock/guardrails/`](agents/bedrock/guardrails/) | Guardrail policy model + labelled red-team set + coverage gate |
| [`agents/bedrock/eval/`](agents/bedrock/eval/) | Verdict-acceptance gate (schema · no-PII · grounding · faithfulness · decision) + decision log |
| [`agents/databricks/`](agents/databricks/) | Tier-3: copilot tools + pyfunc + eval harness |
| [`agents/langgraph/`](agents/langgraph/) | Self-healing Supervisor + Medic (see below) |
| [`simulator/`](simulator/) | ~500 txns/sec synthetic generator with fraud injection |
| [`dashboards/`](dashboards/) | Grafana dashboards (JSON) + data-source provisioning |
| [`deploy/`](deploy/) | Local docker-compose funnel + in-VPC streaming image |
| [`.github/workflows/`](.github/workflows/) | CI (PR validation) + gated bootstrap / deploy / destroy |
| [`docs/`](docs/) | Narrative, plan, data flow, features, integration contracts, deploy runbook |

---

## Beyond the demo

The repo also includes a **LangGraph self-healing layer** (`agents/langgraph/` — Supervisor +
Medic) with deterministic, idempotent remediation for known incident classes (endpoint-latency
rollback, consumer-lag scaling, bounded pipeline retry), covered by **37 tests** with real
LangGraph and mocked signals. It is **tested-only**: live monitoring and real remediation are
cloud-deferred (not wired into the live deploy). It is not promoted here because it has not
been run live.

Other honest deferrals (documented, not hidden): a real-time online Feature Store lookup (the
demo resolves features from a bundled snapshot), Genie NL→SQL, Spark **stateful** streaming
(Gold currently full-recomputes per card), and a scheduled drift monitor. See
[`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## Docs

[NARRATIVE](docs/NARRATIVE.md) · [PROJECT_PLAN](docs/PROJECT_PLAN.md) ·
[data-flow](docs/data-flow.md) · [features](docs/features.md) ·
[bedrock-integration](docs/bedrock-integration.md) · [copilot-design](docs/copilot-design.md) ·
[governance](docs/governance/README.md) · [DEPLOY](docs/DEPLOY.md)

Engineering rules are in [`CLAUDE.md`](CLAUDE.md).
