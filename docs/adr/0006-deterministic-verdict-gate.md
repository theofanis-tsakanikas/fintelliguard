# ADR-0006: A deterministic acceptance gate, not an LLM judge

- **Status:** Accepted
- **Date:** 2026-06 (recorded 2026-08)

## Context

A Tier-2 verdict is a regulated artifact: it cites legal provisions, names the drivers behind a
decision, and recommends an action on someone's money. Something must decide whether a given verdict
is fit to reach an analyst.

The fashionable answer is an LLM judge. It is also the wrong one here. A judge that is itself a
language model is non-deterministic, cannot be replayed against a past decision, drifts when the
model behind it is updated, and — most damning in a regulated setting — cannot explain its own
refusal in terms a compliance officer can audit.

## Decision

Five **deterministic** checks in `agents/bedrock/eval/judge.py`, all of them pure functions over the
verdict and the retrieval context. A verdict must pass all five:

| Check | What it asserts |
|---|---|
| **schema** | The verdict has the required shape and types |
| **no-PII** | No raw identifier survived into the text |
| **grounding** | Every cited `(instrument, article)` appears in the **retrieved context** — set membership, so a fabricated article appended to a real one is refused |
| **faithfulness** | Declared drivers are a **subset** of the model's actual `top_features` — prose cannot decide this |
| **decision** | The agent may **escalate** with a reason and may **never soften** |

The direction rule is the one that matters most: releasing a transaction the model flagged is a human
decision, and no amount of fluent reasoning from the agent is allowed to make it.

Every scored transaction — not only the flagged 1% — writes one replayable `DecisionRecord` under a
correlation id, carrying `model_version` and `guardrail_version`, and refuses to be written if it
would carry raw PII (EU AI Act Art. 12).

## Alternatives rejected

- **An LLM-as-judge.** Non-deterministic, unreplayable, drifts with the judge model, and cannot
  produce an auditable reason for refusal.
- **Regex over the verdict text.** Would catch a malformed citation but not a *fabricated* one — the
  grounding check needs the retrieval context, not the string.
- **Trusting the guardrail alone.** Guardrails cover PII and topic policy; they say nothing about
  whether a cited article was retrieved or whether the drivers match the model.

## Consequences

- A refusal is explainable in one line, replayable, and testable — the gate has its own tests, and
  `make gate-proof` plants a softened verdict and requires the named check to refuse it.
- The gate constrains what the agent may usefully say: it must cite from what it retrieved, and must
  reason from features the model actually used. That is a feature, not a limitation.
- The gate cannot judge whether a *correctly grounded* verdict is well-argued. That remains a human
  question, which is what Tier 3 exists for.
