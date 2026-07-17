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


def test_a_uuid_is_not_a_card_number():
    """Found by gate_proof, which is the point of gate_proof.

    A randomly generated correlation id —

        9e988812-2850-4531-8c49-c36e1c2e94be

    contains `988812-2850-4531-8`: fifteen digits with hyphens, matching the candidate
    pattern exactly. So the decision log refused to record a decision roughly one time in N,
    depending on which UUID it happened to draw. It read as a flaky test; it was a PII
    detector with a random false-positive rate.
    """
    assert not contains_pii("decision 9e988812-2850-4531-8c49-c36e1c2e94be recorded"), (
        f"a UUID was reported as a card number: {found_pii('9e988812-2850-4531-8c49-c36e1c2e94be')}"
    )


def test_no_uuid_is_ever_a_card_number():
    """The property, not one example: a flaky detector needs more than one draw to catch."""
    import uuid

    offenders = [
        str(u) for _ in range(3000) if contains_pii(f"decision {(u := uuid.uuid4())} recorded")
    ]
    assert not offenders, (
        f"{len(offenders)}/3000 correlation ids read as card numbers: {offenders[:3]}"
    )


def test_no_computed_feature_value_is_ever_a_card_number():
    """The property, because three hand-picked floats are not a test.

    `amount_log`, `amount_zscore` and `amount_sum_1h` all render with long mantissas, and
    every decision record carries them. Luhn alone is not enough here: it accepts about one
    random digit run in ten, so ~10% of float mantissas pass it. The three literal examples
    above happened to fail Luhn, which made the boundary rule look redundant — it is not,
    and this is the test that says so.
    """
    import random

    random.seed(7)
    values = [f"amount_log is {random.uniform(1, 10):.17f}" for _ in range(5000)]
    offenders = [v for v in values if contains_pii(v)]
    assert not offenders, (
        f"{len(offenders)}/5000 feature values read as card numbers, e.g. {offenders[:2]} — "
        "every decision record carries values like these"
    )


def test_a_digit_run_that_fails_the_luhn_checksum_is_not_a_card():
    """What separates a card number from a lot of digits."""
    assert not contains_pii("reference 1234567890123456 attached")  # fails Luhn
    assert contains_pii("card 4111111111111111 flagged")  # the classic test Visa, valid
