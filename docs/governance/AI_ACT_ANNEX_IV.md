# EU AI Act — Annex IV Technical Documentation (excerpt)

> Generated from code by `python -m ml.governance.generate`. Do not edit by hand.

## 1. System description & intended purpose

FintelliGuard scores card transactions for fraud and produces documented, regulation-grounded compliance verdicts for suspicious cases. Fraud-prevention decisioning on financial transactions is a **high-risk** use under the AI Act; this document records the controls.

## 2. Risk classification

- **High-risk** (financial decisioning affecting access to / use of payment services).
- Automated decisions are bounded: Tier 1 scores, Tier 2 reasons under guardrails, **Tier 3 is a human analyst** who reviews flagged cases (human oversight, Art. 14).

## 3. Data & data governance

- See the [Dataset Card](DATASET_CARD.md): canonical schema, hashed identifiers, no raw PII in features.
- Train/serve feature parity enforced by test; no target leakage (proven by a future-dated-event test).

## 4. Accuracy, robustness & the model

- See the [Model Card](MODEL_CARD.md). Promotion gate: AUC-ROC ≥ 0.92 AND fraud precision ≥ 0.85.
- Decision bands: review ≥ 0.7, block ≥ 0.9.

## 5. Guardrails & safety controls

- **Input/output guardrail** covering prompt-attack, the `investment-advice` denied topic, PII anonymisation (CREDIT_DEBIT_CARD_NUMBER, NAME, EMAIL), and contextual grounding (threshold 0.75). The guardrail is bound to the agent at an immutable policy version, so every verdict is attributable to a fixed set of rules; a test asserts the binding resolves, because the guardrail was once provisioned and never attached.
- **Indirect prompt injection.** The regulatory corpus is screened at ingestion (`agents/bedrock/kb/chunking.py`): a document the guardrail would block — one that instructs rather than describes, or carries personal data — is refused before it is embedded, so a poisoned regulation cannot be retrieved into a verdict's context as authority. Both system prompts additionally instruct the agent that retrieved text is data, never instructions. The red-team set exercises this `retrieved` surface through the real screen.
- **Scope of the offline red-team score.** `agents/bedrock/guardrails/policy.py` is a signature model standing in for Bedrock's ML classifier so threat coverage can be regression-tested in CI without calling AWS. Its detectors were written from the red-team prompts they are scored against, so the 19/19 figure in the [coverage report](GUARDRAIL_COVERAGE.md) measures that the model is wired to its test set — **not** the classifier that runs in production, and not a safety property. It is quoted here as a regression score and must not be read as a measured block rate. Real evasions the offline model does not catch (encodings, multilingual, obfuscated PANs) are pinned as known gaps in `tests/agents/bedrock/test_pii.py`, not left silent.
- **Output verdict gate** — every compliance verdict must pass deterministic checks before reaching an analyst: fraud_score, reasoning, regulatory_reference, recommended_action, drivers present, no raw PII, every regulatory reference grounded in retrieved text, every driver one of the model's `top_features`, and a decision consistent with the model hint (or an explicitly justified escalation).

## 6. Human oversight

- Tier 3 analysts investigate flagged cases with the Mosaic AI copilot. Verdicts that fail the gate are rejected and never reach an analyst as findings.
- **The agent may escalate, never soften.** `recommended_action` more cautious than the model's `decision_hint` is accepted with a stated reason; anything less cautious is refused outright by the gate. Releasing a transaction the model flagged is a human decision, not a generated one.
- **Bounds on the self-healing agent** (`agents/langgraph/`). It may restore a previously-promoted model version and only that: never a Staging candidate, and only if that version's own metrics still clear the promotion gate (AUC ≥ 0.92, precision ≥ 0.85) — a rollback is a promotion, and the agent is not an exception to the policy. A latency symptom must persist across consecutive samples before it can move a model, and each healing thread has a hard ceiling on total actions, past which it pages a human instead of acting.

## 7. Post-market monitoring

- **Drift detection** (`ml/monitoring/drift.py`): PSI per feature, alert at ≥ 0.25 (+ two-sample KS). **Scope:** this is a library and a threshold, not a running monitor — no job computes drift on a schedule, no reference snapshot is persisted, and no alert sink is wired. Stated plainly because this document previously implied otherwise.
- **Decision records** (`agents/bedrock/eval/decision_log.py`): every scored transaction — not only the flagged ones — writes one replayable record: input keys → the 15 features → `model_version`, score and `top_features` → the verdict, the gate result and the guardrail outcome, under a correlation id. The record refuses to be written if it would carry raw PII. The sink is injected, so retention and immutability are a deployment decision (S3 Object Lock / a Delta table with an audit grant); the local funnel writes append-only JSONL, which has the same contract and none of the guarantees.

## 8. Record-keeping

- This documentation is generated from the codebase and kept in sync by CI (`--check`), so the record always reflects the deployed system.

