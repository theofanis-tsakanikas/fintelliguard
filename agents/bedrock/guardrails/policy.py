"""A Python model of the Bedrock guardrail policy — kept in sync with Terraform.

The live guardrail is provisioned by ``agents/bedrock/terraform/guardrail.tf`` and only
runs in AWS. This module mirrors that policy as deterministic, testable logic so the
threat coverage can be proven **offline, in CI**, against a labelled red-team set
(``redteam.py``) — without calling Bedrock.

It is intentionally a *model*, not a re-implementation of Bedrock's classifiers: it
encodes the same policy classes the Terraform declares (prompt-attack filter, the
``investment-advice`` denied topic, PII anonymisation, contextual grounding) and decides,
for a given input/output, whether that policy class would engage. The coverage test
(``tests/.../test_guardrail_coverage.py``) cross-checks this model against the actual
``guardrail.tf`` so the two can never silently diverge: delete a policy from the
Terraform and the red-team coverage drops, failing CI.

Maps to Responsible-AI Readiness Framework dimension 2 (Guardrails & safety): *input
guardrails for prompt-injection / jailbreak / out-of-scope, output guardrails for PII and
grounding.*
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agents.bedrock.pii import PII_PATTERNS

# Policy-class identifiers (match the Terraform policy blocks).
PROMPT_ATTACK = "PROMPT_ATTACK"
DENIED_TOPIC = "DENIED_TOPIC"
PII = "PII"
GROUNDING = "GROUNDING"

# Threat categories a red-team case can belong to.
CAT_PROMPT_INJECTION = "prompt_injection"
CAT_JAILBREAK = "jailbreak"
CAT_OUT_OF_SCOPE = "out_of_scope"
CAT_PII_LEAK = "pii_leak"
CAT_UNGROUNDED = "ungrounded"
CAT_BENIGN = "benign"


# --------------------------------------------------------------------------- #
# Signatures (the deterministic detectors behind each policy class)
# --------------------------------------------------------------------------- #

# Prompt-injection / jailbreak phrasings. Bedrock's PROMPT_ATTACK filter is an ML
# classifier; this is a high-precision signature list standing in for it offline.
_PROMPT_ATTACK_SIGNATURES = (
    r"ignore (all|any|the) (previous|prior|above) (instructions|prompts?)",
    r"disregard (your|the|all) (instructions|rules|system prompt)",
    r"forget (your|the|all|everything) (instructions|rules|above)",
    r"you are now\b",
    r"act as (if you are|an?)\b",
    r"developer mode",
    r"\bdo anything now\b|\bDAN\b",
    r"reveal (your|the) (system )?(prompt|instructions)",
    r"print (your|the) (system )?(prompt|instructions)",
    r"override (your|the|all) (safety|guardrails?|rules)",
    r"pretend (you are|to be)\b",
    r"bypass (the|your|all) (rules|policy|guardrails?|filters?)",
)

# The agent issues compliance verdicts, NOT financial advice (Terraform denied topic).
_INVESTMENT_ADVICE_SIGNATURES = (
    r"should i (invest|buy|sell|trade)\b",
    r"what (stocks?|shares?|crypto|coins?) should i (buy|sell|invest)",
    r"\b(investment|trading|financial) advice\b",
    r"\bportfolio\b.*\b(recommend|advice|allocate)\b",
    r"is this a good (investment|buy|trade)\b",
)

# PII the guardrail anonymises on input/output (Terraform PII entities). These match a
# *request to expose* raw PII, or raw PII appearing in text.
_PII_REQUEST_SIGNATURES = (
    r"(show|give|tell|reveal|what is|print) (me )?(the )?(full |raw |unmasked |actual )?"
    r"(card|credit card|debit card|pan)\b.*(number|digits)?",
    r"(real|full|legal) name of (the )?(card ?holder|customer|account holder)",
    r"(customer|card ?holder)('|’)s (email|e-?mail) address",
    r"unmask (the )?(card|pii|customer)",
)

# Raw PII value patterns (for output redaction): a bare PAN or email in free text.
# Shared with the verdict gate and the decision log — the three used to carry separate
# copies, and the copy here matched the mantissa of a float, so a verdict quoting
# `amount_log = 3.93182563272432` was blocked as a card-number leak.
_PII_VALUE_PATTERNS = PII_PATTERNS


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


# --------------------------------------------------------------------------- #
# Policy decision
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GuardrailDecision:
    """The outcome of evaluating one text against the guardrail policy."""

    blocked: bool
    policy: str | None  # which policy class engaged (PROMPT_ATTACK / DENIED_TOPIC / PII / None)
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return not self.blocked


@dataclass(frozen=True)
class GuardrailPolicy:
    """The configured guardrail policy classes (mirrors guardrail.tf)."""

    prompt_attack: bool = True
    denied_topics: tuple[str, ...] = ("investment-advice",)
    pii_entities: tuple[str, ...] = ("CREDIT_DEBIT_CARD_NUMBER", "NAME", "EMAIL")
    grounding_threshold: float = 0.75
    relevance_threshold: float = 0.75
    # signature tables (overridable in tests)
    _prompt_attack_sigs: tuple[str, ...] = field(default=_PROMPT_ATTACK_SIGNATURES, repr=False)
    _investment_sigs: tuple[str, ...] = field(default=_INVESTMENT_ADVICE_SIGNATURES, repr=False)
    _pii_request_sigs: tuple[str, ...] = field(default=_PII_REQUEST_SIGNATURES, repr=False)
    _pii_value_pats: tuple[str, ...] = field(default=_PII_VALUE_PATTERNS, repr=False)

    # -- input guardrail ---------------------------------------------------- #

    def evaluate_input(self, text: str) -> GuardrailDecision:
        """Decide whether an incoming user message is blocked, and by which policy."""
        if self.prompt_attack and _matches_any(text, self._prompt_attack_sigs):
            return GuardrailDecision(True, PROMPT_ATTACK, "prompt-attack signature matched")
        if "investment-advice" in self.denied_topics and _matches_any(text, self._investment_sigs):
            return GuardrailDecision(True, DENIED_TOPIC, "denied topic: investment-advice")
        if self.pii_entities and _matches_any(text, self._pii_request_sigs):
            # A request to expose raw PII is refused (the agent never reveals PII).
            return GuardrailDecision(True, PII, "request to expose raw PII")
        return GuardrailDecision(False, None)

    # -- output guardrail --------------------------------------------------- #

    def evaluate_output(
        self, text: str, *, grounding_score: float | None = None
    ) -> GuardrailDecision:
        """Decide whether agent output is blocked: PII leak or ungrounded verdict."""
        if self.pii_entities and _matches_any(text, self._pii_value_pats):
            return GuardrailDecision(True, PII, "raw PII present in output")
        if grounding_score is not None and grounding_score < self.grounding_threshold:
            return GuardrailDecision(
                True, GROUNDING, f"grounding {grounding_score:.2f} < {self.grounding_threshold}"
            )
        return GuardrailDecision(False, None)
