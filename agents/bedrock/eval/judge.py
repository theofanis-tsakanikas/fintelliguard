"""Verdict acceptance gate — a deterministic output guardrail on agent verdicts.

The Bedrock agent returns a structured compliance verdict:

    {fraud_score, reasoning, regulatory_reference, recommended_action}

Before that verdict reaches a human analyst it must pass this gate. The gate is the
deterministic floor under the (deploy-time, LLM-as-judge) evaluation: it enforces the
non-negotiable properties of a regulated verdict, with no model in the loop, so it runs in
CI and at request time identically.

Five checks (all hard — any failure rejects the verdict):

1. **Schema**        — the required fields, correct types.
2. **No raw PII**    — no card numbers / emails leak into the verdict text.
3. **Grounding**     — every provision the verdict cites appears in the retrieved context
                       (no invented article numbers).
4. **Faithfulness**  — every driver the verdict claims is one of the model's actual
                       ``top_features`` (no invented drivers).
5. **Decision**      — ``recommended_action`` is valid, and the agent may escalate beyond
                       the model's ``decision_hint`` but never soften it.

Each of those was bypassable with a one-line edit, and each bypass was verified:

* grounding was ``c in ref or ref in c`` — bidirectional substring containment. So
  ``"PSD2 Art. 97 (SCA) and Art. 999"`` was ACCEPTED: a fabricated provision appended to a
  real one, which is the exact threat the check exists to stop. ``"A"`` was accepted too.
  It now extracts provision tokens and requires set membership.
* faithfulness scanned the prose for 15 hardcoded English phrases. Reasoning that said
  "the cardholder IP reputation and the merchant category code drove this" produced
  ``used = set()`` and therefore ``invented = []`` — two entirely fabricated drivers,
  accepted, all checks green. **A check on prose cannot be complete**: any paraphrase
  ("rapid succession of charges") defeats it, and no alias table fixes that. So the
  verdict now DECLARES its drivers in a structured field, which makes the check decidable
  instead of best-effort. The prose scan survives as a secondary net.
* the decision check applied its cue list symmetrically, so ``decision_hint="block"``,
  ``recommended_action="allow"`` and the word "however" was accepted — the model said stop
  a fraudulent transaction, the agent said let it through, and "however" was the entire
  justification. The riskiest direction was the one it waved through. Softening is now
  refused outright: an agent may be more cautious than the model, never less. Only a human
  releases a transaction the model flagged.

Maps to Readiness Framework dimension 2 (Guardrails & safety): *output guardrails +
response grounding against an approved source, with an eval gate in CI.*
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agents.bedrock.pii import contains_pii
from ml.serving.scorer import DECISION_ALLOW, DECISION_BLOCK, DECISION_REVIEW

REQUIRED_FIELDS = (
    "fraud_score",
    "reasoning",
    "regulatory_reference",
    "recommended_action",
    # The drivers the verdict leans on, as canonical feature names. Added because
    # faithfulness cannot be decided from prose: the old check knew 15 English phrases and
    # silently passed anything else, including two invented drivers in a single sentence.
    # Making the agent name its drivers turns an unbounded NLP problem into set membership.
    "drivers",
)
VALID_ACTIONS = (DECISION_ALLOW, DECISION_REVIEW, DECISION_BLOCK)

# How cautious each action is. The agent may move UP this ladder from the model's hint and
# never down: being more careful than the model is a judgement call, releasing a
# transaction the model flagged is a decision a human makes.
_CAUTION: dict[str, int] = {DECISION_ALLOW: 0, DECISION_REVIEW: 1, DECISION_BLOCK: 2}

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

# Language that states an escalation, rather than merely reads like prose.
#
# Was `("escalat", "however", "despite", "diverg", "overrid", "stronger", "elevat")`.
# "however" is a conjunction, and "elevat" matches "elevated risk" — boilerplate the
# project's own gold verdict emits (`verdicts.py:44`). The bypass token was a phrase the
# agent already writes by default.
_ESCALATION_CUES = (
    "escalat",
    "more stringent",
    "warrants a stricter",
    "overrid",
    "notwithstanding",
)

# --------------------------------------------------------------------------- #
# Provision extraction — the grounding check's unit of comparison
# --------------------------------------------------------------------------- #

# An article/provision number: "Art. 97", "Article 13", "art 22a".
_ARTICLE = re.compile(r"\bart(?:icle)?\.?\s*(\d+[a-z]?)\b", re.IGNORECASE)

# A regulatory instrument by name: PSD2, AMLD5, GDPR, MiFID II, EBA...
_INSTRUMENT = re.compile(r"\b(PSD\d?|AMLD\d?|GDPR|MiFID(?:\s*II)?|EBA|MLR)\b", re.IGNORECASE)

# A guideline reference: "GL 2021/03".
_GUIDELINE = re.compile(r"\bGL\s*(\d{4}/\d+)\b", re.IGNORECASE)


def provisions(text: str) -> set[str]:
    """The regulatory provisions named in `text`, normalised.

    This is the whole grounding fix. Comparing citation strings to context strings with
    `in` cannot distinguish "cites PSD2 Art. 97" from "cites PSD2 Art. 97 and also the
    Art. 999 I made up" — the second contains the first, so containment says yes. Reducing
    both sides to a SET of provisions makes the fabricated one a member that the context
    does not have, and set difference is not fooled by concatenation.
    """
    found: set[str] = set()
    for match in _INSTRUMENT.finditer(text):
        found.add(re.sub(r"\s+", "", match.group(1).upper()))
    for match in _ARTICLE.finditer(text):
        found.add(f"ART.{match.group(1).upper()}")
    for match in _GUIDELINE.finditer(text):
        found.add(f"GL{match.group(1)}")
    return found


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


def _declared_drivers(verdict: Mapping[str, object]) -> list[str]:
    """The canonical feature names the verdict says it relied on."""
    raw = verdict.get("drivers")
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, Sequence):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _mentioned_features(reasoning: str) -> set[str]:
    """Canonical feature names the prose appears to lean on — a BEST-EFFORT signal.

    Only detects the phrases in `DRIVER_ALIASES`. It is a secondary net, not the
    faithfulness check: it was the whole check once, and reasoning that said "the cardholder
    IP reputation and the merchant category code drove this" returned an empty set, so two
    invented drivers passed as zero invented drivers. Absence of evidence was read as
    evidence of absence. `drivers` is the check; this catches self-contradiction.
    """
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


def _ungrounded_provisions(citation: str, context_refs: Sequence[str]) -> tuple[set[str], bool]:
    """Provisions in `citation` that the retrieved context does not contain.

    Returns `(fabricated, recognisable)`. `recognisable` is False when the citation names
    no provision at all — `"A"` used to pass, being a substring of everything.
    """
    cited = provisions(citation)
    if not cited:
        return set(), False
    available: set[str] = set()
    for ref in context_refs:
        available |= provisions(ref)
    return cited - available, True


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
    pii_ok = not contains_pii(blob)
    checks["no_pii"] = pii_ok
    if not pii_ok:
        failures.append("pii: raw PII present in the verdict")

    # 3. grounding — every PROVISION cited must appear in the retrieved context
    citations = _cited_references(reference)
    fabricated: set[str] = set()
    unrecognisable: list[str] = []
    for citation in citations:
        missing_provisions, recognisable = _ungrounded_provisions(
            citation, context.retrieved_references
        )
        fabricated |= missing_provisions
        if not recognisable:
            unrecognisable.append(citation)

    grounding_ok = bool(citations) and not fabricated and not unrecognisable
    checks["grounding"] = grounding_ok
    if not citations:
        failures.append("grounding: verdict cites no regulatory_reference")
    if unrecognisable:
        failures.append(f"grounding: citations name no identifiable provision: {unrecognisable}")
    if fabricated:
        failures.append(
            f"grounding: provisions not in the retrieved context: {sorted(fabricated)} — "
            "the verdict cites regulation that was never retrieved"
        )

    # 4. faithfulness — the declared drivers must be the model's actual top_features
    declared = _declared_drivers(verdict)
    top = set(context.top_features)
    invented = sorted(set(declared) - top)

    # Secondary net: the prose must not lean on a KNOWN driver the verdict did not declare.
    # This cannot be complete — an unaliased paraphrase slips through, which is exactly why
    # `drivers` exists — but it does catch reasoning that contradicts its own driver list.
    undeclared_in_prose = sorted(_mentioned_features(reasoning) - set(declared) - top)

    faithful_ok = bool(declared) and not invented and not undeclared_in_prose
    checks["faithfulness"] = faithful_ok
    if not declared:
        failures.append("faithfulness: verdict declares no drivers")
    if invented:
        failures.append(f"faithfulness: drivers not in the model's top_features: {invented}")
    if undeclared_in_prose:
        failures.append(
            f"faithfulness: reasoning leans on drivers the verdict never declared and the "
            f"model never produced: {undeclared_in_prose}"
        )

    # 5. decision validity + direction of travel
    if action not in VALID_ACTIONS:
        checks["decision"] = False
        failures.append(f"decision: recommended_action {action!r} not in {VALID_ACTIONS}")
    elif context.decision_hint not in _CAUTION:
        checks["decision"] = False
        failures.append(f"decision: model hint {context.decision_hint!r} is not a valid action")
    else:
        checks["decision"] = _check_direction(action, context.decision_hint, reasoning, failures)

    return GateResult(accepted=not failures, failures=tuple(failures), checks=checks)


def _check_direction(action: str, hint: str, reasoning: str, failures: list[str]) -> bool:
    """The agent may escalate with a reason; it may never soften the model's decision."""
    if action == hint:
        return True

    if _CAUTION[action] < _CAUTION[hint]:
        # The direction that used to be waved through on the word "however".
        failures.append(
            f"decision: softens the model's {hint!r} to {action!r}. The agent may be more "
            "cautious than the model, never less — releasing a flagged transaction is a "
            "human decision, not a generated one"
        )
        return False

    if not any(cue in reasoning.lower() for cue in _ESCALATION_CUES):
        failures.append(
            f"decision: escalates {hint!r} to {action!r} without stating why in the reasoning"
        )
        return False
    return True
