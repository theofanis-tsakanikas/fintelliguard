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

# A card-number-like digit run: 13-19 digits, optionally spaced or hyphenated.
#
# The lookarounds are the fix, and each half earns its place:
#
#   (?<![\d.])   not preceded by a digit or a decimal point — this is what excludes the
#                mantissa of `3.9318256327243257`. `\b` alone does not: it sits happily
#                between `.` and `9`, because `.` is a non-word character.
#   (?!\.?\d)    not followed by more digits, with or without a decimal point — excludes
#                `1234567890123.45`. It deliberately does NOT reject a plain trailing
#                period: "the card 4111 1111 1111 1111." is a sentence, and the first
#                version of this fix rejected exactly that, which the tests caught.
CARD_NUMBER = r"(?<![\d.])(?:\d[ -]?){13,19}(?!\.?\d)"

# An email address in free text.
EMAIL = r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"

PII_PATTERNS: tuple[str, ...] = (CARD_NUMBER, EMAIL)


def contains_pii(text: str) -> bool:
    """True if `text` carries a raw PAN or email address."""
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in PII_PATTERNS)


def found_pii(text: str) -> list[str]:
    """Every raw-PII substring in `text` — for diagnosing a rejection."""
    hits: list[str] = []
    for pattern in PII_PATTERNS:
        hits.extend(match.group(0) for match in re.finditer(pattern, text, flags=re.IGNORECASE))
    return hits
