# Responsible-AI Governance

Proof that FintelliGuard's AI is **safe, grounded, monitored, and documented** — not just
that it works. Every artifact here is generated from, or enforced against, the real code,
and gated in CI. Maps to the Responsible-AI Readiness Framework (Guardrails & safety ·
Observability & drift · Governance as code).

## Controls

| Control | Code | What it proves | CI gate |
|---|---|---|---|
| **Guardrail red-team** | `agents/bedrock/guardrails/` | Prompt-injection, jailbreak, out-of-scope, and PII-leak probes are blocked by the configured guardrail; benign traffic is not. | `evaluate.py` block-rate must be 100%, FP-rate 0% — and `guardrail.tf` must still declare each policy class. |
| **Verdict acceptance gate** | `agents/bedrock/eval/` | Every Tier-2 compliance verdict passes 5 deterministic checks — schema, no raw PII, grounding (cited regs ∈ retrieved context), faithfulness (drivers ∈ model `top_features`), decision consistency — before it reaches an analyst. | Labelled verdict set: gold accepted, each adversarial verdict rejected on the right check. |
| **Drift monitoring** | `ml/monitoring/drift.py` | Feature-distribution drift between training reference and live traffic is detected (PSI + two-sample KS) with alert thresholds, before it silently degrades the score. | Detector unit-tested on synthetic shifts. |
| **Regulated-AI docs** | `ml/governance/generate.py` | EU-AI-Act Annex IV technical documentation, model card, and dataset card are generated from the code (features, thresholds, guardrail coverage, gate, drift bands). | `--check` fails the build if the committed docs drift from the code. |

## Generated artifacts (do not edit by hand — `make govern-docs`)

- [MODEL_CARD.md](MODEL_CARD.md) — the XGBoost scorer: 15 features + ranges, decision bands, promotion gate, explainability, limitations, monitoring.
- [DATASET_CARD.md](DATASET_CARD.md) — data provenance, schema/parity, PII handling.
- [GUARDRAIL_COVERAGE.md](GUARDRAIL_COVERAGE.md) — the live red-team coverage report.
- [AI_ACT_ANNEX_IV.md](AI_ACT_ANNEX_IV.md) — Annex IV technical documentation tying the controls together (high-risk classification, human oversight, post-market monitoring).

## Why deterministic, not "an LLM judge"

The gates are rule-based on purpose: they form the **hard floor** under the agent, run
identically in CI and at request time, and need no model in the loop. In production an
LLM-as-judge can score verdicts as an *additional* signal — but a verdict that fails
grounding, leaks PII, or invents a driver is rejected by this deterministic gate
regardless. That is the trust layer: the AI is allowed to reason, but not to ship an
unsafe or ungrounded output.

## Commands

```bash
make guardrail-scan    # red-team coverage gate (block-rate / false-positive-rate)
make govern-docs       # regenerate the cards + AI-Act docs from the code
python -m agents.bedrock.guardrails.evaluate   # the same gate, verbose
```
