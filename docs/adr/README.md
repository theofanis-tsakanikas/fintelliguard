# Architecture Decision Records

What was chosen, what was rejected, and what it cost. The decisions were made during development
(2026-06) and recorded here in 2026-08 — the reasoning is drawn from the code, the Terraform
comments, `CLAUDE.md` and `docs/NARRATIVE.md`, not reconstructed after the fact.

| ADR | Decision | Rejected |
|---|---|---|
| [0001](0001-three-tier-decisioning-funnel.md) | Three tiers, each more expensive on a smaller slice of traffic | One LLM for everything · rules for the fast path · compliance narrative generated later in batch |
| [0002](0002-two-clouds-joined-by-a-contract.md) | Two clouds joined by the `get_fraud_score()` contract | Everything on Databricks · a shared Delta table as the interface · a public endpoint with a static key |
| [0003](0003-interpretable-feature-contract.md) | 14 features that are explainable *and* computable online | The anonymised `V1–V339` · a 15th feature faked as a constant |
| [0004](0004-promotion-gate-thresholds.md) | AUC ≥ 0.83 **and** precision ≥ 0.85, fail-closed | AUC ≥ 0.92 (unreachable by this feature set) · AUC alone · recall · human sign-off |
| [0005](0005-foundation-model-selection.md) | Nova Lite wired, Claude Haiku 4.5 switchable | Haiku as the default (account-gated) · Sonnet (cost) · a hardcoded model id |
| [0006](0006-deterministic-verdict-gate.md) | Five deterministic checks before a verdict reaches an analyst | LLM-as-judge · regex over the text · trusting the guardrail alone |
| [0007](0007-streaming-run-then-destroy.md) | Streaming as an opt-in stage, proven by a probe | MSK standing permanently · local Kafka for the cloud demo · serverless MSK |

## The one that keeps coming back

[ADR-0004](0004-promotion-gate-thresholds.md) is the record worth reading first. It is the decision
where the honest number and the impressive number disagreed, and the reasoning — *an unreachable gate
is not a gate, it is theatre that gets quietly lowered the first time it blocks a release* — is the
principle the rest of the Responsible-AI layer is built on.

## Format

Each record states **Context** (the forces, including what was tried first), **Decision** (what was
chosen, with the code that implements it), **Alternatives rejected** (and why), and **Consequences**
(including the ones that hurt). A decision that turns out to be wrong is superseded by a new record,
not edited — the ledger keeps the mistake.
