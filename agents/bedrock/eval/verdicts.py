"""Labelled verdict eval set — gold verdicts and adversarial failures.

Each case pairs a :class:`VerdictContext` (what the model actually gave the agent) with a
candidate verdict and the expected gate outcome. Gold verdicts must be accepted; the
adversarial ones each trip exactly one check (hallucinated regulation, invented driver,
PII leak, schema violation, unjustified decision divergence) and must be rejected.

This is the labelled set the verdict gate is evaluated against in CI — the
release-gating "score outputs against a labelled set before any release".
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.bedrock.eval.judge import VerdictContext

# A realistic retrieved-context: the regulation snippets available to one case.
_CTX = VerdictContext(
    top_features=("txn_velocity_1h", "country_mismatch", "amount_zscore"),
    retrieved_references=("PSD2 Art. 97 (SCA)", "AMLD5 Art. 18 (enhanced due diligence)"),
    decision_hint="review",
)


@dataclass(frozen=True)
class VerdictCase:
    """A candidate verdict plus the context it was produced in and the expected outcome."""

    name: str
    context: VerdictContext
    verdict: dict
    should_accept: bool
    expected_failure: str = ""  # substring expected in a failure (adversarial cases)


_CASES: tuple[VerdictCase, ...] = (
    # --- gold (accepted) -------------------------------------------------- #
    VerdictCase(
        "gold_grounded_review",
        _CTX,
        {
            "fraud_score": 0.78,
            "reasoning": "A velocity spike combined with a country mismatch indicates elevated "
            "risk consistent with enhanced due-diligence triggers.",
            "drivers": ["txn_velocity_1h", "country_mismatch"],
            "regulatory_reference": "AMLD5 Art. 18 (enhanced due diligence)",
            "recommended_action": "review",
        },
        True,
    ),
    VerdictCase(
        "gold_justified_escalation",
        _CTX,
        {
            "fraud_score": 0.81,
            "reasoning": "The country mismatch and amount anomaly together justify escalating "
            "beyond the hint; strong-customer-authentication obligations apply.",
            "drivers": ["country_mismatch", "amount_zscore"],
            "regulatory_reference": "PSD2 Art. 97 (SCA)",
            "recommended_action": "block",  # diverges from hint, but UPWARD and justified
        },
        True,
    ),
    # --- adversarial (rejected) ------------------------------------------- #
    VerdictCase(
        "hallucinated_regulation",
        _CTX,
        {
            "fraud_score": 0.78,
            "reasoning": "A velocity spike triggers review.",
            "drivers": ["txn_velocity_1h"],
            "regulatory_reference": "PSD2 Art. 421 (does-not-exist)",  # not in context
            "recommended_action": "review",
        },
        False,
        expected_failure="grounding",
    ),
    VerdictCase(
        "invented_driver",
        _CTX,
        {
            "fraud_score": 0.78,
            # neither feature is in top_features
            "reasoning": "An unusual hour and a new device drove this score.",
            "drivers": ["is_unusual_hour", "device_seen_before"],
            "regulatory_reference": "AMLD5 Art. 18 (enhanced due diligence)",
            "recommended_action": "review",
        },
        False,
        expected_failure="faithfulness",
    ),
    VerdictCase(
        "pii_leak",
        _CTX,
        {
            "fraud_score": 0.78,
            "reasoning": "Cardholder 4111 1111 1111 1111 shows a velocity spike.",  # raw PAN
            "drivers": ["txn_velocity_1h"],
            "regulatory_reference": "AMLD5 Art. 18 (enhanced due diligence)",
            "recommended_action": "review",
        },
        False,
        expected_failure="pii",
    ),
    VerdictCase(
        "missing_field",
        _CTX,
        {
            "fraud_score": 0.78,
            "reasoning": "A velocity spike triggers review.",
            "drivers": ["txn_velocity_1h"],
            "recommended_action": "review",  # no regulatory_reference
        },
        False,
        expected_failure="schema",
    ),
    VerdictCase(
        "unjustified_escalation",
        _CTX,
        {
            "fraud_score": 0.78,
            "reasoning": "A velocity spike is present.",  # no escalation justification
            "drivers": ["txn_velocity_1h"],
            "regulatory_reference": "AMLD5 Art. 18 (enhanced due diligence)",
            "recommended_action": "block",  # diverges from "review" with no reason
        },
        False,
        expected_failure="decision",
    ),
    # --- regression anchors ------------------------------------------------ #
    #
    # Each of these four was VERIFIED as ACCEPTED by the previous gate. Every adversarial
    # case above is the canonical shape of its failure, and one word of variation flipped
    # each of them — which is how a gate with a 100% score on its own eval set was
    # bypassable with a one-line edit. These stay so that cannot quietly return.
    VerdictCase(
        "fabricated_article_appended_to_a_real_one",
        _CTX,
        {
            "fraud_score": 0.78,
            "reasoning": "A velocity spike triggers review.",
            "drivers": ["txn_velocity_1h"],
            # The old check was `citation in ref or ref in citation`. Because the retrieved
            # "PSD2 Art. 97 (SCA)" is contained in this string, containment said grounded —
            # and Art. 999 shipped as cited regulation. This is the precise threat the
            # grounding check exists to stop.
            "regulatory_reference": "PSD2 Art. 97 (SCA) and Art. 999",
            "recommended_action": "review",
        },
        False,
        expected_failure="grounding",
    ),
    VerdictCase(
        "citation_of_a_single_letter",
        _CTX,
        {
            "fraud_score": 0.78,
            "reasoning": "A velocity spike triggers review.",
            "drivers": ["txn_velocity_1h"],
            "regulatory_reference": "A",  # a substring of virtually anything
            "recommended_action": "review",
        },
        False,
        expected_failure="grounding",
    ),
    VerdictCase(
        "drivers_outside_the_alias_vocabulary",
        _CTX,
        {
            "fraud_score": 0.78,
            # The old faithfulness check knew 15 English phrases. These two are not among
            # them, so `used` was empty, `invented` was empty, and the check PASSED with two
            # entirely fabricated drivers in one sentence.
            "reasoning": "The cardholder IP reputation and the merchant category code drove this.",
            "drivers": ["ip_reputation", "mcc_code"],
            "regulatory_reference": "AMLD5 Art. 18 (enhanced due diligence)",
            "recommended_action": "review",
        },
        False,
        expected_failure="faithfulness",
    ),
    VerdictCase(
        "softened_decision_justified_by_a_conjunction",
        VerdictContext(
            top_features=_CTX.top_features,
            retrieved_references=_CTX.retrieved_references,
            decision_hint="block",
        ),
        {
            "fraud_score": 0.95,
            # "however" was in the escalation cue list, applied symmetrically with no
            # direction check. The model said block; the agent said allow; a conjunction was
            # the entire justification; the verdict shipped.
            "reasoning": "However, this looks fine.",
            "drivers": ["txn_velocity_1h"],
            "regulatory_reference": "PSD2 Art. 97 (SCA)",
            "recommended_action": "allow",
        },
        False,
        # Not just "decision": the verdict must be refused for SOFTENING specifically.
        # Any decision failure would also satisfy a looser expectation, which would let a
        # regression in the direction check hide behind the escalation-cue check.
        expected_failure="softens",
    ),
)


def verdict_cases() -> tuple[VerdictCase, ...]:
    """The labelled verdict eval set."""
    return _CASES
