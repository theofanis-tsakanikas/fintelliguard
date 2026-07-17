"""Raw-PII detection must catch card numbers and not catch arithmetic.

The pattern was written out three times — the guardrail policy, the verdict gate, the
decision log — and every copy did this:

    r"\b(?:\d[ -]?){13,19}\b"

`\b` is a word boundary, and `.` is a non-word character, so the boundary sits happily
between `3.` and `9318256327243257`. The mantissa of a float is sixteen consecutive digits.
Every verdict quoting a computed feature was a card-number leak, and the guardrail's
false-positive rate is a number this project publishes to a regulator.

Both directions matter and both are asserted here: a control that blocks legitimate output
is not a stricter control, it is a broken one.
"""

from __future__ import annotations

import pytest

from agents.bedrock.pii import contains_pii, found_pii

CARD_NUMBERS = [
    pytest.param("card 4111111111111111 flagged", id="unspaced"),
    pytest.param("Cardholder 4111 1111 1111 1111.", id="spaced, ends a sentence"),
    pytest.param("PAN 4111-1111-1111-1111 seen", id="hyphenated"),
    pytest.param("378282246310005", id="15-digit amex, bare"),
]

NOT_PII = [
    pytest.param("amount_log is 3.9318256327243257", id="float mantissa — the bug"),
    pytest.param("sum 1234567890123.45 eur", id="long float with decimals"),
    pytest.param("score 0.9318256327243257, high", id="mantissa before a comma"),
    pytest.param("txn_velocity_1h was 4", id="a short number"),
    pytest.param("merchant M00001 tier 5", id="an identifier"),
]


@pytest.mark.parametrize("text", CARD_NUMBERS)
def test_a_card_number_is_pii(text):
    assert contains_pii(text), f"a raw PAN went undetected: {found_pii(text)}"


@pytest.mark.parametrize("text", NOT_PII)
def test_arithmetic_is_not_pii(text):
    assert not contains_pii(text), (
        f"a number that is not a card was reported as PII: {found_pii(text)} — this blocks "
        "legitimate verdicts and inflates the published false-positive rate"
    )


def test_an_email_is_pii():
    assert contains_pii("contact analyst@bank.example.com about the case")


def test_the_pattern_has_exactly_one_definition():
    """Three copies of a security control are three chances for it to drift.

    `country_mismatch` already inverted at training because `modal_value` was implemented
    twice and the two resolved ties differently. The PII pattern was in three files.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    copies = [
        path
        for path in (repo / "agents").rglob("*.py")
        if "13,19" in path.read_text(encoding="utf-8")
    ]
    assert [p.name for p in copies] == ["pii.py"], (
        f"the card-number pattern is defined in {[p.name for p in copies]} — it belongs in "
        "agents/bedrock/pii.py alone"
    )
