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

- **Input/output guardrail** covering prompt-attack, the `investment-advice` denied topic, PII anonymisation (CREDIT_DEBIT_CARD_NUMBER, NAME, EMAIL), and contextual grounding (threshold 0.75). Red-team coverage: **16/16 blocked** (see [Guardrail Coverage](GUARDRAIL_COVERAGE.md)).
- **Output verdict gate** — every compliance verdict must pass deterministic checks before reaching an analyst: fraud_score, reasoning, regulatory_reference, recommended_action present, no raw PII, every regulatory reference grounded in retrieved text, every driver one of the model's `top_features`, and a decision consistent with the model hint (or an explicitly justified escalation).

## 6. Human oversight

- Tier 3 analysts investigate flagged cases with the Mosaic AI copilot; the agent never auto-executes an irreversible action. Low-confidence / ungrounded verdicts are rejected by the gate and routed to human review.

## 7. Post-market monitoring

- **Drift detection** (`ml/monitoring/drift.py`): PSI per feature, alert at ≥ 0.25; every inference is logged (input → features → model → guardrails → output) for audit.

## 8. Record-keeping

- This documentation is generated from the codebase and kept in sync by CI (`--check`), so the record always reflects the deployed system.

