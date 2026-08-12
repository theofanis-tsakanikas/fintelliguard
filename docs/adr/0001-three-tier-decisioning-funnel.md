# ADR-0001: A three-tier decisioning funnel, not one model per transaction

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

Two requirements pull in opposite directions. Fraud scoring must happen **before** authorisation, in
tens of milliseconds, on every transaction — a mid-size issuer sees thousands per second. AML and
PSD2 separately require a **documented, regulation-grounded justification** for every transaction
that gets flagged.

One system cannot do both. A model fast enough to score every transaction cannot produce a cited
compliance narrative; a model that can reason over regulation cannot run in 50 ms, and would cost
more per transaction than the fraud it prevents.

## Decision

Split the decision into three tiers, each more expensive than the last and each running on a smaller
slice of the traffic:

| Tier | What it is | Volume | Latency |
|---|---|---|---|
| 1 | XGBoost on Mosaic AI Model Serving | **every** transaction | <50 ms |
| 2 | Bedrock agent, RAG over AML/PSD2, guardrailed | the suspicious **~1%** | seconds |
| 3 | Analyst copilot over Vector Search | human-initiated | asynchronous |

The funnel (`ml/serving/funnel.py`) escalates from Tier 1 to Tier 2 automatically on
`decision_hint ∈ {review, block}` — no human in that loop — and `allow` simply clears. Tier 3 is
never automatic: it is a tool an analyst picks up.

## Alternatives rejected

- **One LLM for everything.** Cannot meet the latency budget, and the per-transaction cost is
  absurd against the ~99% of traffic that is plainly legitimate.
- **One model, with the compliance narrative generated later in batch.** Loses the property that
  matters: the justification exists *at the moment of the decision*, under the same correlation id.
- **Rules for the fast path, model for the slow path.** The usual arrangement, and the reason rule
  engines flag after approval. Scoring every transaction is the point.

## Consequences

- Cost tracks risk rather than volume — the expensive reasoning runs on 1% of traffic.
- Each tier can be evaluated on its own terms: AUC and precision for Tier 1, a deterministic
  acceptance gate for Tier 2, routing quality for Tier 3.
- The seam between tiers becomes an interface that must be specified, versioned and tested — see
  [ADR-0002](0002-two-clouds-joined-by-a-contract.md).
- A transaction can be approved at Tier 1 and later found fraudulent with no Tier-2 record, because
  Tier 2 never saw it. That is the accepted cost of not reasoning over everything.
