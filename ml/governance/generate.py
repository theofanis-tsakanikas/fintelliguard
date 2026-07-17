"""Generate the regulated-AI documentation from the system itself.

FintelliGuard is a high-risk AI system under the EU AI Act (fraud decisioning on financial
transactions). This module renders the technical documentation a regulator expects —
**from the code**, not from hand-written prose, so it can never drift from what actually
ships:

* ``docs/governance/MODEL_CARD.md``        — the XGBoost scorer: intended use, the 15
  features and ranges, decision bands, the promotion gate, explainability, limitations.
* ``docs/governance/DATASET_CARD.md``      — training data provenance, schema, PII handling.
* ``docs/governance/GUARDRAIL_COVERAGE.md`` — the red-team coverage report (live number).
* ``docs/governance/AI_ACT_ANNEX_IV.md``   — Annex IV-style technical documentation tying
  the controls together (risk classification, human oversight, logging, accuracy + the
  guardrail / output-gate / drift controls).

Facts are pulled from the real modules (``schema.FEATURE_SPECS``, ``promote`` thresholds,
``scorer.ScoringConfig`` bands, the guardrail policy, the verdict gate, the drift bands),
so editing a threshold in code updates the documentation on the next ``make govern-docs``.
CI asserts the committed docs match a fresh render (``--check``).

Maps to Readiness Framework dimension 4 (Governance as code): *EU-AI-Act technical
documentation generated from the system and kept in sync; model/dataset cards versioned.*
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agents.bedrock.eval.judge import REQUIRED_FIELDS
from agents.bedrock.guardrails.evaluate import evaluate_coverage
from agents.bedrock.guardrails.policy import GuardrailPolicy
from ml.features.schema import FEATURE_SPECS
from ml.monitoring.drift import PSI_MINOR, PSI_SIGNIFICANT
from ml.serving.scorer import ScoringConfig
from ml.training.promote import AUC_THRESHOLD, FRAUD_PRECISION_THRESHOLD

DOCS = Path("docs/governance")


def _feature_rows() -> list[str]:
    rows = []
    for spec in FEATURE_SPECS:
        rng = []
        if spec.minimum is not None:
            rng.append(f"{'>' if spec.exclusive_min else '>='} {spec.minimum:g}")
        if spec.maximum is not None:
            rng.append(f"{'<' if spec.exclusive_max else '<='} {spec.maximum:g}")
        if spec.allowed is not None:
            rng = [f"in {list(spec.allowed)}"]
        rows.append(f"| `{spec.name}` | {spec.dtype.__name__} | {' '.join(rng) or '—'} |")
    return rows


def render_model_card() -> str:
    cfg = ScoringConfig()
    out = [
        "# Model Card — FintelliGuard Fraud Scorer",
        "",
        "> Generated from code by `python -m ml.governance.generate`. Do not edit by hand.",
        "",
        "## Overview",
        "",
        "- **Model:** XGBoost binary classifier (`binary:logistic`).",
        "- **Task:** real-time fraud probability for a single card transaction (Tier 1).",
        "- **Output contract:** `fraud_score`, `model_version`, `threshold`, `decision_hint`, "
        "`top_features` (per-prediction TreeSHAP contributions).",
        "- **Role in the system:** scores 100% of transactions; the ~1% above the review "
        "threshold are escalated to the Tier-2 Bedrock compliance agent.",
        "",
        "## Inputs — the canonical 15 features",
        "",
        "Both adapters (stream + IEEE-CIS) must produce exactly this schema; parity is enforced "
        "by test. Ranges are the DLT validation gates.",
        "",
        "| Feature | Type | Valid range |",
        "| --- | --- | --- |",
        *_feature_rows(),
        "",
        "## Decision bands",
        "",
        f"- **review threshold:** {cfg.review_threshold:g} (Tier-2 trigger)",
        f"- **block threshold:** {cfg.block_threshold:g}",
        f"- Scores in `[{cfg.review_threshold:g}, {cfg.block_threshold:g})` → `review`; "
        f"`>= {cfg.block_threshold:g}` → `block`; else `allow`.",
        "",
        "## Promotion gate (Staging → Production)",
        "",
        f"Promotion requires **AUC-ROC ≥ {AUC_THRESHOLD}** AND **fraud-class precision ≥ "
        f"{FRAUD_PRECISION_THRESHOLD}** on the held-out test set. Missing metrics fail closed.",
        "",
        "## Explainability",
        "",
        "Every score ships `top_features`: exact per-prediction TreeSHAP contributions "
        "(`pred_contribs`), i.e. *why this transaction* scored as it did — not global importance. "
        "These drive the Tier-2 agent's reasoning and the verdict faithfulness check.",
        "",
        "## Limitations & known risks",
        "",
        "- Trained on synthetic + IEEE-CIS data; absolute rates are illustrative, not "
        "production-measured.",
        "- A fraud label is never a feature; window features use only strictly-prior transactions "
        "(no target leakage — proven by test).",
        "- Distribution shift degrades the score silently — monitored by the drift detector "
        "(`ml/monitoring/drift.py`), PSI bands below.",
        "",
        "## Monitoring",
        "",
        f"- **Drift:** PSI per feature — stable `< {PSI_MINOR}`, watch `[{PSI_MINOR}, "
        f"{PSI_SIGNIFICANT})`, alert `≥ {PSI_SIGNIFICANT}` (+ two-sample KS).",
        "- **Output:** every Tier-2 verdict passes the verdict acceptance gate before reaching an "
        "analyst.",
        "",
    ]
    return "\n".join(out) + "\n"


def render_dataset_card() -> str:
    out = [
        "# Dataset Card — FintelliGuard Training Data",
        "",
        "> Generated from code by `python -m ml.governance.generate`. Do not edit by hand.",
        "",
        "## Sources",
        "",
        "- **IEEE-CIS Fraud Detection** (public benchmark) — adapted to the canonical 15-feature "
        "schema.",
        "- **Synthetic transaction stream** — the simulator (`simulator/`), ~500 txns/s, with "
        "injected fraud archetypes; adapted by `ml/features/adapter_stream.py`.",
        "",
        "## Schema & parity",
        "",
        f"One canonical schema of **{len(FEATURE_SPECS)} features** "
        "(`ml/features/schema.py`). Both "
        "adapters must emit it identically — a single end-to-end test compares them, eliminating "
        "training/serving skew by construction.",
        "",
        "## Personal data handling",
        "",
        "- Card identifiers are hashed (`card_hash`); no raw PAN, name, or email enters the "
        "feature store.",
        "- The Tier-2 agent's guardrail anonymises `CREDIT_DEBIT_CARD_NUMBER`, `NAME`, `EMAIL` on "
        "I/O; "
        "the verdict gate rejects any raw PII in output.",
        "",
        "## Labels",
        "",
        "- Binary fraud label, used only as the training target — never as a model input.",
        "",
    ]
    return "\n".join(out) + "\n"


def render_guardrail_coverage() -> str:
    report = evaluate_coverage()
    rows = [
        f"| {cat} | {b['blocked']}/{b['total']} | {b['correct']}/{b['total']} |"
        for cat, b in sorted(report.per_category.items())
    ]
    out = [
        "# Guardrail Coverage — Red-Team Report",
        "",
        "> Generated from code by `python -m ml.governance.generate`. Do not edit by hand.",
        "",
        "The input/output guardrail (`agents/bedrock/terraform/guardrail.tf`, modelled offline in "
        "`agents/bedrock/guardrails/policy.py`) is evaluated against a labelled red-team set.",
        "",
        f"- **Adversarial probes blocked:** {report.blocked_adversarial}/{report.adversarial} "
        f"({report.block_rate * 100:.0f}%)",
        f"- **Benign false positives:** {report.false_positives}/{report.benign} "
        f"({report.false_positive_rate * 100:.0f}%)",
        "",
        "| Threat category | Blocked | Correct |",
        "| --- | --- | --- |",
        *rows,
        "",
        "Coverage is enforced in CI: the block rate must be 100% and the false-positive rate 0%. "
        "The coverage test also parses `guardrail.tf` so removing a policy class fails the build.",
        "",
    ]
    return "\n".join(out) + "\n"


def render_ai_act() -> str:
    policy = GuardrailPolicy()
    cov = evaluate_coverage()
    cfg = ScoringConfig()
    out = [
        "# EU AI Act — Annex IV Technical Documentation (excerpt)",
        "",
        "> Generated from code by `python -m ml.governance.generate`. Do not edit by hand.",
        "",
        "## 1. System description & intended purpose",
        "",
        "FintelliGuard scores card transactions for fraud and produces documented, "
        "regulation-grounded "
        "compliance verdicts for suspicious cases. Fraud-prevention decisioning on financial "
        "transactions is a **high-risk** use under the AI Act; this document records the controls.",
        "",
        "## 2. Risk classification",
        "",
        "- **High-risk** (financial decisioning affecting access to / use of payment services).",
        "- Automated decisions are bounded: Tier 1 scores, Tier 2 reasons under guardrails, "
        "**Tier 3 is a "
        "human analyst** who reviews flagged cases (human oversight, Art. 14).",
        "",
        "## 3. Data & data governance",
        "",
        "- See the [Dataset Card](DATASET_CARD.md): canonical schema, hashed identifiers, no raw "
        "PII in features.",
        "- Train/serve feature parity enforced by test; no target leakage (proven by a "
        "future-dated-event test).",
        "",
        "## 4. Accuracy, robustness & the model",
        "",
        "- See the [Model Card](MODEL_CARD.md). Promotion gate: AUC-ROC ≥ "
        f"{AUC_THRESHOLD} AND fraud precision ≥ {FRAUD_PRECISION_THRESHOLD}.",
        f"- Decision bands: review ≥ {cfg.review_threshold:g}, block ≥ {cfg.block_threshold:g}.",
        "",
        "## 5. Guardrails & safety controls",
        "",
        f"- **Input/output guardrail** covering prompt-attack, the "
        f"`{', '.join(policy.denied_topics)}` "
        f"denied topic, PII anonymisation ({', '.join(policy.pii_entities)}), and contextual "
        f"grounding "
        f"(threshold {policy.grounding_threshold:g}). The guardrail is bound to the agent at "
        "an immutable policy version, so every verdict is attributable to a fixed set of "
        "rules; a test asserts the binding resolves, because the guardrail was once "
        "provisioned and never attached.",
        "- **Indirect prompt injection.** The regulatory corpus is screened at ingestion "
        "(`agents/bedrock/kb/chunking.py`): a document the guardrail would block — one that "
        "instructs rather than describes, or carries personal data — is refused before it is "
        "embedded, so a poisoned regulation cannot be retrieved into a verdict's context as "
        "authority. Both system prompts additionally instruct the agent that retrieved text "
        "is data, never instructions. The red-team set exercises this `retrieved` surface "
        "through the real screen.",
        "- **Scope of the offline red-team score.** `agents/bedrock/guardrails/policy.py` is "
        "a signature model standing in for Bedrock's ML classifier so threat coverage can be "
        "regression-tested in CI without calling AWS. Its detectors were written from the "
        f"red-team prompts they are scored against, so the {cov.blocked_adversarial}/"
        f"{cov.adversarial} figure in the [coverage report](GUARDRAIL_COVERAGE.md) measures "
        "that the model is wired to its test set — **not** the classifier that runs in "
        "production, and not a safety property. It is quoted here as a regression score and "
        "must not be read as a measured block rate. Real evasions the offline model does not "
        "catch (encodings, multilingual, obfuscated PANs) are pinned as known gaps in "
        "`tests/agents/bedrock/test_pii.py`, not left silent.",
        "- **Output verdict gate** — every compliance verdict must pass deterministic checks "
        "before "
        f"reaching an analyst: {', '.join(REQUIRED_FIELDS)} present, no raw PII, every regulatory "
        "reference grounded in retrieved text, every driver one of the model's `top_features`, "
        "and a "
        "decision consistent with the model hint (or an explicitly justified escalation).",
        "",
        "## 6. Human oversight",
        "",
        "- Tier 3 analysts investigate flagged cases with the Mosaic AI copilot. Verdicts that "
        "fail the gate are rejected and never reach an analyst as findings.",
        "- **The agent may escalate, never soften.** `recommended_action` more cautious than "
        "the model's `decision_hint` is accepted with a stated reason; anything less cautious "
        "is refused outright by the gate. Releasing a transaction the model flagged is a human "
        "decision, not a generated one.",
        "- **Bounds on the self-healing agent** (`agents/langgraph/`). It may restore a "
        "previously-promoted model version and only that: never a Staging candidate, and only "
        f"if that version's own metrics still clear the promotion gate (AUC ≥ {AUC_THRESHOLD}, "
        f"precision ≥ {FRAUD_PRECISION_THRESHOLD}) — a rollback is a promotion, and the agent "
        "is not an exception to the policy. A latency symptom must persist across consecutive "
        "samples before it can move a model, and each healing thread has a hard ceiling on "
        "total actions, past which it pages a human instead of acting.",
        "",
        "## 7. Post-market monitoring",
        "",
        f"- **Drift detection** (`ml/monitoring/drift.py`): PSI per feature, "
        f"alert at ≥ {PSI_SIGNIFICANT} (+ two-sample KS). **Scope:** this is a library and "
        "a threshold, not a running monitor — no job computes drift on a schedule, no "
        "reference snapshot is persisted, and no alert sink is wired. Stated plainly "
        "because this document previously implied otherwise.",
        "- **Decision records** (`agents/bedrock/eval/decision_log.py`): every scored "
        "transaction — not only the flagged ones — writes one replayable record: input keys "
        "→ the 15 features → `model_version`, score and `top_features` → the verdict, the "
        "gate result and the guardrail outcome, under a correlation id. The record refuses "
        "to be written if it would carry raw PII. The sink is injected, so retention and "
        "immutability are a deployment decision (S3 Object Lock / a Delta table with an "
        "audit grant); the local funnel writes append-only JSONL, which has the same "
        "contract and none of the guarantees.",
        "",
        "## 8. Record-keeping",
        "",
        "- This documentation is generated from the codebase and kept in sync by CI (`--check`), "
        "so the "
        "record always reflects the deployed system.",
        "",
    ]
    return "\n".join(out) + "\n"


def all_artifacts(root: Path) -> dict[Path, str]:
    return {
        root / DOCS / "MODEL_CARD.md": render_model_card(),
        root / DOCS / "DATASET_CARD.md": render_dataset_card(),
        root / DOCS / "GUARDRAIL_COVERAGE.md": render_guardrail_coverage(),
        root / DOCS / "AI_ACT_ANNEX_IV.md": render_ai_act(),
    }


def _default_root() -> Path:
    # ml/governance/generate.py → repo root is three parents up.
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate regulated-AI documentation from the code."
    )
    parser.add_argument("--root", default=str(_default_root()))
    parser.add_argument("--check", action="store_true", help="fail if committed docs are stale")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    artifacts = all_artifacts(root)

    if args.check:
        stale = [
            str(p.relative_to(root))
            for p, content in artifacts.items()
            if not p.is_file() or p.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("STALE governance docs (run `make govern-docs`):")
            for p in stale:
                print(f"  - {p}")
            return 1
        print("governance docs are up to date.")
        return 0

    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
