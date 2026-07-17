"""Raw-PII detection must catch card numbers and not catch arithmetic.

The pattern was written out three times — the guardrail policy, the verdict gate, the
decision log — and every copy used a bare word-boundary regex around a 13-to-19 digit run.

`\b` is a word boundary, and `.` is a non-word character, so the boundary sits happily
between `3.` and `9318256327243257`. The mantissa of a float is sixteen consecutive digits.
Every verdict quoting a computed feature was a card-number leak, and the guardrail's
false-positive rate is a number this project publishes to a regulator.

Both directions matter and both are asserted here: a control that blocks legitimate output
is not a stricter control, it is a broken one.
"""

from __future__ import annotations

import random
import re

import pytest

from agents.bedrock.pii import _CARD_CANDIDATE, _luhn_valid, contains_pii, found_pii

# Chosen BECAUSE its corpus contains UUIDs whose digit runs pass Luhn — the exact case the
# grouping rule exists to reject. See `test_the_uuid_corpus_actually_contains_the_phenomenon`.
_UUID_CORPUS_SEED = 7

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


def _uuid_like(rng: random.Random) -> str:
    """A UUID-shaped hex string from a SEEDED generator.

    `uuid.uuid4()` reads os.urandom and cannot be seeded, so a property test over it is a
    coin flip: the ~2-in-20,000 false positives appear on some runs and not others. A
    probabilistic gate is not a gate — `gate_proof` caught exactly that, reporting the
    attack as failing "for an unrelated reason" on the runs where the flake did not fire.
    """
    return "-".join(
        "".join(rng.choice("0123456789abcdef") for _ in range(n)) for n in (8, 4, 4, 4, 12)
    )


def _uuid_corpus(seed: int = _UUID_CORPUS_SEED, n: int = 20_000) -> list[str]:
    rng = random.Random(seed)
    return [_uuid_like(rng) for _ in range(n)]


def test_no_uuid_is_ever_a_card_number():
    """A generated correlation id must never be mistaken for PII.

    `9e988812-2850-4531-8c49-...` contains `988812-2850-4531-8` — fifteen digits with
    hyphens — and ~10% of such runs pass Luhn, so the audit log refused roughly one decision
    in ten thousand, at random. It presented as a flaky test.
    """
    corpus = _uuid_corpus()
    offenders = [u for u in corpus if contains_pii(f"decision {u} recorded")]
    assert not offenders, (
        f"{len(offenders)}/{len(corpus)} correlation ids read as card numbers: {offenders[:3]}"
    )


def test_the_uuid_corpus_actually_contains_the_phenomenon():
    """The corpus must hold UUIDs that WOULD be misread without the grouping rule.

    The first seed chosen yielded zero such UUIDs in 20,000 draws, so the test above passed
    whether or not the rule existed — coverage theatre. `gate_proof` caught it by reporting
    the attack as failing "for an unrelated reason". A corpus that does not contain the
    phenomenon proves nothing about the control that handles it, which is the same
    self-deception as a gate nobody has attacked.
    """
    would_leak = [
        u
        for u in _uuid_corpus()
        for m in _CARD_CANDIDATE.finditer(f"decision {u} recorded")
        if _luhn_valid(re.sub(r"[ -]", "", m.group(0)))
    ]
    assert would_leak, (
        f"seed {_UUID_CORPUS_SEED} yields no UUID whose digit run passes Luhn, so "
        "test_no_uuid_is_ever_a_card_number cannot detect the grouping rule being removed. "
        "Pick a seed whose corpus contains the case."
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


def test_the_guardrail_runs_the_same_detector_as_the_gate_and_the_log():
    """Import the FUNCTION, not the pattern.

    `policy.py` imported `PII_PATTERNS` and re-ran `re.search` itself, so it kept the
    PRE-Luhn detector while the verdict gate and the decision log ran the fixed one: "one
    definition, used by every control that claims to find it" was one definition and three
    behaviours. `test_the_pattern_has_exactly_one_definition` passed throughout, because it
    greps for the literal `"13,19"` — a string-in-file assertion, in the suite whose whole
    thesis is that those cannot fail for any reason worth catching.
    """
    from agents.bedrock.guardrails.policy import GuardrailPolicy

    guardrail = GuardrailPolicy()
    for text in [t.values[0] for t in NOT_PII] + [
        "amount_log is 3.9318256327243257",
        "decision 9e988812-2850-4531-8c49-c36e1c2e94be recorded",
    ]:
        assert guardrail.evaluate_output(text).allowed == (not contains_pii(text)), (
            f"the guardrail and the detector disagree on {text!r} — the guardrail is "
            "running a different implementation of the same control"
        )
    for text in [t.values[0] for t in CARD_NUMBERS]:
        assert guardrail.evaluate_output(text).blocked, f"the guardrail missed a PAN: {text!r}"


# Evasions that DEFEAT this detector. Pinned so the gap is documented, not discovered.
KNOWN_EVASIONS = [
    pytest.param("4539.5787.6362.1486", id="dot separators"),
    pytest.param("4539/5787/6362/1486", id="slash separators"),
    pytest.param("4539‍5787‍6362‍1486", id="zero-width joiners"),
    pytest.param("4539 5787 6362 1486", id="non-breaking spaces"),
    pytest.param("txn-4539578763621486", id="embedded in an identifier"),
    pytest.param("45395787-6362-1486-9abc-def012345678", id="formatted as a UUID"),
    pytest.param("45395787\n63621486", id="split across lines"),
    pytest.param("Cardholder John Smith", id="a name (declared but not modelled)"),
    pytest.param("SSN 123-45-6789", id="an SSN (not modelled)"),
]


@pytest.mark.parametrize("text", KNOWN_EVASIONS)
def test_known_evasions_are_documented_not_claimed(text):
    """These are NOT caught, and that is stated rather than discovered.

    A regex cannot defend against deliberate obfuscation — the live control is Bedrock's
    PII classifier; this module is the offline model of it. The test exists so the boundary
    of the claim is executable: if a future change starts catching one of these, this test
    fails and the docstring gets updated. Silence is not coverage, and a security control's
    documented limits are part of the control.
    """
    assert not contains_pii(text), (
        f"{text!r} is now DETECTED — good, but agents/bedrock/pii.py still documents it as "
        "an evasion this detector misses. Update the docstring; the boundary of a claim is "
        "part of the claim."
    )
