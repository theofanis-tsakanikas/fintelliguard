"""The synthetic case seed must be reproducible, informative, and impossible to mistake
for real institutional knowledge."""

from __future__ import annotations

from dataclasses import fields

import pytest

from agents.databricks.cases import (
    CASE_ID_PREFIX,
    DISCLOSURE,
    PROVENANCE,
    ResolvedCase,
    build_seed_cases,
    outcome_mix,
    resolved_cases_schema,
)

CASES = build_seed_cases()


# --------------------------------------------------------------------------- #
# The honesty guarantee — this is why the module exists in this shape
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_every_case_declares_itself_synthetic_in_all_three_places(case: ResolvedCase):
    """A marker in a metadata column is a marker the analyst never sees.

    `case_text` is the column Vector Search EMBEDS and returns, so the disclosure has to
    open it — that is the copy that reaches a human asking "have we seen this before?".
    The id prefix and the `provenance` column are the machine-readable belt and braces.
    """
    assert case.case_id.startswith(CASE_ID_PREFIX)
    assert case.provenance == PROVENANCE
    assert case.case_text.startswith(DISCLOSURE), (
        "the disclosure must OPEN case_text — it is the retrieved, human-visible column"
    )


def test_the_disclosure_survives_truncation_to_a_search_preview():
    """A UI that previews the first ~200 characters must still show the disclosure.

    Retrieval surfaces are lossy: a result list shows a snippet, not the whole case. If the
    marker only survives when the full text is rendered, it does not survive.
    """
    for case in CASES:
        assert DISCLOSURE[:120] in case.case_text[:200]


# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #


def test_the_seed_is_deterministic():
    """Same seed -> byte-identical fixture. A fixture that rerolls every run is not a
    fixture; it silently changes what the demo shows and what these tests assert."""
    assert build_seed_cases(seed=42) == build_seed_cases(seed=42)


def test_a_different_seed_produces_a_different_fixture():
    """Guards the inverse: a generator that ignores its seed would pass the determinism
    test trivially while being unable to produce a second corpus."""
    assert build_seed_cases(seed=42) != build_seed_cases(seed=7)


def test_count_is_honoured_and_zero_is_refused():
    assert len(build_seed_cases(count=15)) == 15
    with pytest.raises(ValueError):
        build_seed_cases(count=0)


# --------------------------------------------------------------------------- #
# The fixture has to be USEFUL, not merely present
# --------------------------------------------------------------------------- #


def test_every_archetype_carries_both_outcomes():
    """The value of "have we seen this?" is the RATIO, not the recall.

    A corpus where every case is confirmed fraud teaches an analyst nothing — it can only
    ever confirm the flag. Every archetype must contain at least one case that turned out
    to be a false positive, or the index cannot express "four were fraud, one was a
    traveller", which is the sentence the analyst actually needs.
    """
    for archetype, mix in outcome_mix(CASES).items():
        assert mix.get("confirmed_fraud", 0) > 0, f"{archetype}: no confirmed fraud"
        assert mix.get("false_positive", 0) > 0, (
            f"{archetype}: no false positive — the index could only ever agree with the model"
        )


def test_cases_sit_in_the_band_that_actually_reaches_an_analyst():
    """A resolved case is one the funnel FLAGGED. A case scored below the review
    threshold never reached a human, so it could not have been resolved by one."""
    from ml.serving.scorer import ScoringConfig

    threshold = ScoringConfig().review_threshold
    for case in CASES:
        assert case.fraud_score >= threshold, f"{case.case_id} was never flagged"
        assert case.decision in {"review", "block"}


def test_archetypes_are_the_funnels_own_not_invented():
    """A case built on an archetype the simulator cannot produce would surface as a
    "similar case" resembling nothing the system ever sees."""
    from simulator.generator import FraudPattern

    known = {p.value for p in FraudPattern}
    assert {c.archetype for c in CASES} == known


# --------------------------------------------------------------------------- #
# The table contract
# --------------------------------------------------------------------------- #


def test_the_ddl_cannot_drift_from_the_dataclass():
    """`resolved_cases_schema()` is what creates the Delta table the index syncs from.
    If it drifts from `ResolvedCase`, the write fails at deploy — far from this test."""
    declared = [col.strip().split()[0] for col in resolved_cases_schema().split(",")]
    assert declared == [f.name for f in fields(ResolvedCase)]


def test_the_index_key_and_embedded_column_are_present_and_non_empty():
    """`vector_search.yml` pins `primary_key: case_id` and embeds `case_text`."""
    ids = [c.case_id for c in CASES]
    assert len(set(ids)) == len(ids), "case_id is the index primary key — must be unique"
    assert all(c.case_text.strip() for c in CASES)


def test_the_columns_the_copilot_reads_back_are_populated():
    """`search_similar_cases.RESULT_COLUMNS` is what the analyst is shown."""
    from agents.databricks.tools.search_similar_cases import RESULT_COLUMNS

    for case in CASES:
        row = case.as_row()
        for column in RESULT_COLUMNS:
            assert column in row, f"{column} is retrieved by the tool but not seeded"
            assert str(row[column]).strip(), f"{case.case_id}.{column} is empty"
