"""Raw-PII detection — one definition, used by every control that claims to find it.

**What this is, and what it is not.** It is a high-precision net for PANs and emails
written AS PANs and emails. It is NOT a defence against deliberate obfuscation, and no
regex is: an adversarial review defeated the previous version with dot separators, slash
separators, zero-width joiners, non-breaking spaces, a PAN split across two JSON fields,
and a PAN formatted as a UUID. Those remain undetected here — see
`tests/agents/bedrock/test_pii.py::test_known_evasions_are_documented_not_claimed`, which
pins them so nobody mistakes silence for coverage.

The live control is Bedrock's PII classifier, attached to the agent
(`agents/bedrock/terraform/guardrail.tf`). This module is the offline model of it: a
deterministic net that runs in CI and at request time, catches the case that actually
happens — a PAN in text — and refuses to pretend to more. It also models only
`CREDIT_DEBIT_CARD_NUMBER` and `EMAIL`; `GuardrailPolicy.pii_entities` declares NAME too,
and NAME is not modelled here, which is stated rather than left to be discovered.

**One definition.** The pattern was written out three times — the guardrail policy model,
the verdict gate, the decision log. Three copies of a security control are three chances
for it to drift, and this project has already paid for that shape once (`modal_value` was
implemented twice and the two resolved ties differently, silently inverting
`country_mismatch` at training). Import the FUNCTIONS, not the patterns: the guardrail
imported `PII_PATTERNS` and kept running the pre-Luhn detector while the gate and the log
ran the fixed one — "one definition" that was three behaviours.

**Two bugs this file exists because of**, both found by executing it rather than reading it:

* `r"\b(?:\d[ -]?){13,19}\b"` matches the mantissa of a float. `amount_log =
  3.9318256327243257` is sixteen consecutive digits, and `\b` sits happily after a decimal
  point — so every verdict quoting a computed feature was reported as leaking a card
  number. A guardrail that blocks correct output is not stricter; it is broken, and its
  false-positive rate is a number this project publishes to a regulator.
* Then Luhn alone still misread ~10% of random digit runs, so a generated correlation id
  (`9e988812-2850-4531-8c49-...`) refused roughly one decision in ten thousand, at random.
  It presented as a flaky test.
"""

from __future__ import annotations

import re

# How a card number may be GROUPED. This is what tells a PAN from a UUID, and it replaces
# a mask that was actively unsafe.
#
# The mask looked like this, with this justification:
#
#     "a PAN is grouped 4-4-4-4 and cannot match this shape, so no card number can hide
#      inside it"
#
# That claim is false, and an adversarial review proved it:
#
#     45395787-6362-1486-9abc-def012345678
#     ^^^^^^^^^^^^^^^^^^  -> 4539578763621486, a Luhn-VALID card number
#
# An 8-4-4 split of the first sixteen digits is a legal UUID prefix, and the mask ran BEFORE
# the card scan — so any PAN could be laundered past the verdict gate and the audit log by
# formatting it as a UUID. The mask traded a rare false positive for a total bypass, which
# for a guardrail is the wrong direction: a false negative is the failure that matters.
#
# Issuers group PANs in a small number of ways (4-4-4-4 for most, 4-6-5 for Amex), and none
# of them is 8-4-4. So instead of masking a shape we hope is safe, we require the grouping
# to be one a card actually uses. A UUID fails on its group of 8; the incidental
# `988812-2850-4531-8` fails on its trailing group of 1.
_MIN_GROUP, _MAX_GROUP = 3, 6
_MIN_GROUPS, _MAX_GROUPS = 3, 5

# A CANDIDATE card-number run: 13-19 digits, optionally spaced or hyphenated.
#
# The lookarounds each earn their place:
#
#   (?<![-\d.A-Za-z])  not preceded by a digit, a decimal point, a hyphen or a letter. The
#                      digit/dot half excludes the mantissa of `3.9318256327243257` — `\b`
#                      alone does not, since it sits happily between `.` and `9`. The
#                      letter/hyphen half stops a run being read out of the middle of an
#                      identifier.
#   (?![A-Za-z])       not immediately followed by a letter — same reason.
#   (?!\.?\d)          not followed by more digits, with or without a decimal point:
#                      excludes `1234567890123.45`. It deliberately does NOT reject a plain
#                      trailing period, because "the card 4111 1111 1111 1111." is a
#                      sentence, and the first version of this fix rejected exactly that.
#
# A candidate is still not a finding: see `_is_card_number`.
_CARD_CANDIDATE = re.compile(r"(?<![-\d.A-Za-z])(?:\d[ -]?){13,19}(?![A-Za-z])(?!\.?\d)")

# An email address in free text.
EMAIL = r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"

# Kept for the guardrail's policy model, which reasons about patterns rather than calling
# the detector. CARD_NUMBER is the candidate pattern; the Luhn check refines it.
CARD_NUMBER = _CARD_CANDIDATE.pattern
PII_PATTERNS: tuple[str, ...] = (CARD_NUMBER, EMAIL)

_EMAIL_RE = re.compile(EMAIL, re.IGNORECASE)


def _luhn_valid(digits: str) -> bool:
    """The checksum every real card number satisfies.

    This is what separates a card number from any other run of digits, and it was the
    missing half of the detector. `gate_proof` found it: a randomly generated UUID —

        9e988812-2850-4531-8c49-c36e1c2e94be

    contains `988812-2850-4531-8`, fifteen digits with hyphens, which matched the candidate
    pattern exactly. So the decision log refused to record roughly one decision in N, at
    random, depending on the correlation id it happened to draw. A PII detector with a
    random false-positive rate is not a strict detector; it is a broken one, and its
    false-positive rate is a number this project publishes to a regulator.

    Luhn costs a few lines and makes the check mean "this is a card number" instead of
    "this is a lot of digits". A random 15-digit run passes it about 10% of the time; a real
    PAN passes it always.
    """
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _grouping_is_card_like(candidate: str) -> bool:
    """Is this grouped the way a card number is grouped?

    Unbroken is fine. Otherwise every group must be `_MIN_GROUP`-`_MAX_GROUP` digits and
    there must be `_MIN_GROUPS`-`_MAX_GROUPS` of them — which covers 4-4-4-4, 4-6-5 (Amex)
    and 4-4-4-4-3 (19-digit), and excludes a UUID's 8-4-4 and the stray 6-4-4-1 that a
    random correlation id throws up.
    """
    groups = [g for g in re.split(r"[ -]", candidate.strip(" -")) if g]
    if len(groups) == 1:
        return True  # unbroken digits
    if not _MIN_GROUPS <= len(groups) <= _MAX_GROUPS:
        return False
    return all(_MIN_GROUP <= len(g) <= _MAX_GROUP for g in groups)


def _is_card_number(candidate: str) -> bool:
    """Three independent filters, because each catches what the others miss.

    Boundary (in the regex) rejects runs embedded in identifiers and float mantissas;
    grouping rejects shapes no issuer uses; Luhn rejects digits that are not a card number.
    Any one of them alone has been demonstrably defeated.
    """
    digits = re.sub(r"[ -]", "", candidate)
    if not 13 <= len(digits) <= 19:
        return False
    return _grouping_is_card_like(candidate) and _luhn_valid(digits)


def found_pii(text: str) -> list[str]:
    """Every raw-PII substring in `text` — for diagnosing a rejection.

    Scans the text AS GIVEN. An earlier version masked UUIDs first, which hid a PAN
    formatted as one; nothing is pre-removed now, and the filters in `_is_card_number` do
    the discriminating.
    """
    hits = [m.group(0) for m in _EMAIL_RE.finditer(text)]
    hits.extend(m.group(0) for m in _CARD_CANDIDATE.finditer(text) if _is_card_number(m.group(0)))
    return hits


def contains_pii(text: str) -> bool:
    """True if `text` carries a raw PAN or email address."""
    return bool(found_pii(text))
