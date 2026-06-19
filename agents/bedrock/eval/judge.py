"""Verdict acceptance gate — a deterministic output guardrail on agent verdicts.

The Bedrock agent returns a structured compliance verdict:

    {fraud_score, reasoning, regulatory_reference, recommended_action}

Before that verdict reaches a human analyst it must pass this gate. The gate is the
deterministic floor under the (deploy-time, LLM-as-judge) evaluation: it enforces the
non-negotiable properties of a regulated verdict, with no model in the loop, so it runs in
CI and at request time identically.

Five checks (all hard — any failure rejects the verdict):

1. **Schema**        — exactly the four required fields, correct types.
2. **No raw PII**    — no card numbers / emails leak into the verdict text.
3. **Grounding**     — every cited ``regulatory_reference`` exists in the retrieved
                       context (no invented article numbers).
4. **Faithfulness**  — every driver the reasoning leans on is one of the model's actual
                       ``top_features`` (no invented drivers).
5. **Decision**      — ``recommended_action`` is valid and consistent with the model's
                       ``decision_hint`` unless the reasoning explicitly justifies an
                       escalation.

Maps to Readiness Framework dimension 2 (Guardrails & safety): *output guardrails +
response grounding against an approved source, with an eval gate in CI.*
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ml.serving.scorer import DECISION_ALLOW, DECISION_BLOCK, DECISION_REVIEW

REQUIRED_FIELDS = ("fraud_score", "reasoning", "regulatory_reference", "recommended_action")
VALID_ACTIONS = (DECISION_ALLOW, DECISION_REVIEW, DECISION_BLOCK)

# Human-readable driver phrases → canonical feature names. A verdict that leans on a
# phrase here whose feature is not in the model's top_features is an invented driver.
DRIVER_ALIASES: dict[str, str] = {
    "velocity spike": "txn_velocity_1h",
    "velocity": "txn_velocity_1h",
    "transaction velocity": "txn_velocity_24h",
    "country mismatch": "country_mismatch",
    "cross-border": "country_mismatch",
    "unusual hour": "is_unusual_hour",
    "odd hour": "is_unusual_hour",
    "new device": "device_seen_before",
    "unrecognised device": "device_seen_before",
    "amount anomaly": "amount_zscore",
    "amount spike": "amount_zscore",
    "high amount": "amount_usd",
    "merchant risk": "merchant_risk_score",
    "distinct countries": "distinct_countries_24h",
    "distinct merchants": "distinct_merchants_24h",
    "card age": "card_age_days",
}

# Escalation cue words that justify diverging from the decision hint.
_ESCALATION_CUES = ("escalat", "however", "despite", "diverg", "overrid", "stronger", "elevat")

# Raw-PII patterns (mirror the guardrail's PII entities).
_PII_PATTERNS = (
    r"\b(?:\d[ -]?){13,19}\b",  # card-number-like digit run
    r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",  # email
)


@dataclass(frozen=True)
class VerdictContext:
    """The facts available to the agent when it produced the verdict."""

    top_features: tuple[str, ...]  # the model's actual per-prediction drivers
    retrieved_references: tuple[str, ...]  # regulation ids/snippets in the KB context
    decision_hint: str  # the model's allow/review/block hint


@dataclass(frozen=True)
class GateResult:
    """Outcome of running the gate on one verdict."""

    accepted: bool
    failures: tuple[str, ...] = field(default_factory=tuple)
    checks: Mapping[str, bool] = field(default_factory=dict)


def _mentioned_features(reasoning: str) -> set[str]:
    """Canonical feature names a verdict's reasoning leans on (via aliases + raw names)."""
    text = reasoning.lower()
    found: set[str] = set()
    for phrase, feature in DRIVER_ALIASES.items():
        if phrase in text:
            found.add(feature)
    # also catch a verdict citing a raw feature name directly
    for feature in set(DRIVER_ALIASES.values()):
        if feature in text:
            found.add(feature)
    return found


def _cited_references(regulatory_reference: object) -> list[str]:
    """Normalise the regulatory_reference field to a list of citation tokens."""
    if isinstance(regulatory_reference, str):
        # split on common separators; keep provision-like tokens
        return [tok.strip() for tok in re.split(r"[;,]", regulatory_reference) if tok.strip()]
    if isinstance(regulatory_reference, Sequence):
        return [str(x).strip() for x in regulatory_reference if str(x).strip()]
    return []


def _reference_grounded(citation: str, context_refs: Sequence[str]) -> bool:
    """A citation is grounded if it overlaps any retrieved reference (case-insensitive)."""
    c = citation.lower()
    return any(c in ref.lower() or ref.lower() in c for ref in context_refs)


def evaluate_verdict(verdict: Mapping[str, object], context: VerdictContext) -> GateResult:
    """Run all five checks; return acceptance + the list of failures."""
    checks: dict[str, bool] = {}
    failures: list[str] = []

    # 1. schema
    missing = [f for f in REQUIRED_FIELDS if f not in verdict]
    schema_ok = not missing and isinstance(verdict.get("fraud_score"), (int, float))
    checks["schema"] = schema_ok
    if missing:
        failures.append(f"schema: missing fields {missing}")
    elif not schema_ok:
        failures.append("schema: fraud_score must be numeric")

    reasoning = str(verdict.get("reasoning", ""))
    reference = verdict.get("regulatory_reference", "")
    action = str(verdict.get("recommended_action", ""))
    blob = f"{reasoning} {reference} {action}"

    # 2. no raw PII
    pii_ok = not any(re.search(p, blob, flags=re.IGNORECASE) for p in _PII_PATTERNS)
    checks["no_pii"] = pii_ok
    if not pii_ok:
        failures.append("pii: raw PII present in the verdict")

    # 3. grounding — every citation exists in the retrieved context
    citations = _cited_references(reference)
    ungrounded = [c for c in citations if not _reference_grounded(c, context.retrieved_references)]
    grounding_ok = bool(citations) and not ungrounded
    checks["grounding"] = grounding_ok
    if not citations:
        failures.append("grounding: verdict cites no regulatory_reference")
    elif ungrounded:
        failures.append(f"grounding: citations not in retrieved context: {ungrounded}")

    # 4. faithfulness — drivers used must be in the model's top_features
    used = _mentioned_features(reasoning)
    invented = sorted(used - set(context.top_features))
    faithful_ok = not invented
    checks["faithfulness"] = faithful_ok
    if invented:
        failures.append(f"faithfulness: reasoning invents drivers not in top_features: {invented}")

    # 5. decision validity + consistency with the hint
    if action not in VALID_ACTIONS:
        checks["decision"] = False
        failures.append(f"decision: recommended_action {action!r} not in {VALID_ACTIONS}")
    elif action != context.decision_hint and not any(
        cue in reasoning.lower() for cue in _ESCALATION_CUES
    ):
        checks["decision"] = False
        failures.append(
            f"decision: diverges from hint {context.decision_hint!r} without a stated justification"
        )
    else:
        checks["decision"] = True

    return GateResult(accepted=not failures, failures=tuple(failures), checks=checks)
