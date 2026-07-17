"""Raw-PII detection — one definition, used by every control that claims to find it.

The same pattern was written out three times: the guardrail policy model, the verdict gate,
and the decision log. Three copies of a security control are three chances for it to drift,
and the project has already paid for this shape once (`modal_value` was implemented twice
and the two resolved ties differently, silently inverting `country_mismatch` at training).

The pattern also had a real bug, found by feeding it a feature vector:

    r"\\b(?:\\d[ -]?){13,19}\\b"

matches the mantissa of a float. `amount_log = 3.9318256327243257` contains sixteen
consecutive digits, so any verdict or record quoting a computed feature was reported as
leaking a card number. A guardrail that blocks legitimate output on a float is not a
stricter guardrail — it is a broken one, and its false-positive rate is a published number
in `docs/governance/GUARDRAIL_COVERAGE.md`.

The fix is boundary discipline: a card number is not preceded or followed by a digit or a
decimal point.
"""

from __future__ import annotations

import re

# A UUID. Masked out before the card scan, because it is a generated identifier with a
# fixed 8-4-4-4-12 hex shape and cannot be a card number — and because it was producing
# false positives at a rate that mattered:
#
#     9e988812-2850-4531-8c49-c36e1c2e94be
#            ^^^^^^^^^^^^^^^^^^ fifteen digits with hyphens
#
# Correlation ids and transaction ids are both UUIDs here, so roughly one decision in ten
# thousand was refused at random. Masking is safe in the direction that matters: a PAN is
# grouped 4-4-4-4 and cannot match this shape, so no card number can hide inside it.
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)

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


def _is_card_number(candidate: str) -> bool:
    digits = re.sub(r"[ -]", "", candidate)
    return 13 <= len(digits) <= 19 and _luhn_valid(digits)


def found_pii(text: str) -> list[str]:
    """Every raw-PII substring in `text` — for diagnosing a rejection.

    Three filters, each removing a different class of false positive that was real:
    mask known identifiers, require the run to stand alone rather than sit inside a token,
    and require it to satisfy Luhn. A detector that says "this is a lot of digits" is not a
    card-number detector.
    """
    hits = [m.group(0) for m in _EMAIL_RE.finditer(text)]
    scannable = _UUID.sub("<uuid>", text)
    hits.extend(
        m.group(0) for m in _CARD_CANDIDATE.finditer(scannable) if _is_card_number(m.group(0))
    )
    return hits


def contains_pii(text: str) -> bool:
    """True if `text` carries a raw PAN or email address."""
    return bool(found_pii(text))
